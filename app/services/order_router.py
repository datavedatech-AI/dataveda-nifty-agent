import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import OrderRequest, OrderResult, OrderSide, OrderStatus
from app.models.signal import SignalAction, TradingViewSignal
from app.risk.manager import check_risk
from app.storage.models import Tenant
from app.storage.repository import finalize_signal_log, log_trade, try_reserve_signal
from app.ws.relay_broker import WSRelayBroker

logger = logging.getLogger(__name__)


def _side_and_quantity(signal: TradingViewSignal) -> tuple[OrderSide, float] | None:
    if signal.action == SignalAction.BUY:
        return OrderSide.BUY, signal.quantity
    if signal.action == SignalAction.SELL:
        return OrderSide.SELL, signal.quantity
    if signal.action == SignalAction.CLOSE_LONG:
        return OrderSide.SELL, signal.quantity
    if signal.action == SignalAction.CLOSE_SHORT:
        return OrderSide.BUY, signal.quantity
    # close_all needs a live position lookup, which this MVP doesn't fetch
    # over the relay (see WSRelayBroker.get_open_positions). Use
    # close_long/close_short with an explicit quantity instead.
    return None


async def process_signal(
    signal: TradingViewSignal, tenant: Tenant, session: AsyncSession, broker: WSRelayBroker
) -> OrderResult:
    # Captured before any DB call that might roll back: try_reserve_signal
    # rolls back on a duplicate signal_id, which expires every ORM object
    # loaded in this session (including `tenant`) - touching tenant.* after
    # that without a fresh await would crash with a MissingGreenlet error.
    tenant_id = tenant.id

    reservation = await try_reserve_signal(
        session,
        tenant_id=tenant_id,
        signal_id=signal.signal_id,
        strategy=signal.strategy,
        symbol=signal.symbol,
        action=signal.action.value,
        quantity=signal.quantity,
    )
    if reservation is None:
        logger.info("Duplicate signal_id=%s for tenant=%s ignored", signal.signal_id, tenant_id)
        return OrderResult(broker="mt5", status=OrderStatus.REJECTED, message="Duplicate signal_id, already processed")

    risk_result = await check_risk(tenant, signal, session)
    if not risk_result.allowed:
        logger.warning("Signal %s (tenant=%s) rejected by risk manager: %s", signal.signal_id, tenant.id, risk_result.reason)
        await finalize_signal_log(session, reservation, OrderStatus.REJECTED.value, f"Risk check failed: {risk_result.reason}")
        return OrderResult(broker="mt5", status=OrderStatus.REJECTED, message=risk_result.reason)

    mt5_symbol = (tenant.symbol_map or {}).get(signal.symbol)
    if not mt5_symbol:
        message = f"No MT5 symbol mapped for {signal.symbol} - add it via PUT /me/symbol-map"
        await finalize_signal_log(session, reservation, OrderStatus.REJECTED.value, message)
        return OrderResult(broker="mt5", status=OrderStatus.REJECTED, message=message)

    resolved = _side_and_quantity(signal)
    if resolved is None:
        message = "close_all is not supported yet - use close_long/close_short with an explicit quantity"
        await finalize_signal_log(session, reservation, OrderStatus.REJECTED.value, message)
        return OrderResult(broker="mt5", status=OrderStatus.REJECTED, message=message)
    side, quantity = resolved

    order_request = OrderRequest(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        action=signal.action,
        side=side,
        quantity=quantity,
        order_type=signal.order_type,
        mt5_symbol=mt5_symbol,
        price=signal.price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
    )

    result = await broker.place_order(order_request)

    await log_trade(
        session,
        tenant_id=tenant.id,
        signal_id=f"{signal.signal_id}:mt5",
        strategy=signal.strategy,
        symbol=signal.symbol,
        action=signal.action.value,
        side=side.value,
        quantity=quantity,
        status=result.status.value,
        message=result.message,
        broker_order_id=result.broker_order_id,
    )
    await finalize_signal_log(session, reservation, "dispatched", f"Routed to MT5: {result.status.value}")

    return result
