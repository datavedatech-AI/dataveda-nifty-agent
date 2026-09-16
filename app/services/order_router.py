import logging

from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter
from app.config import Settings
from app.models.order import OrderRequest, OrderResult, OrderSide, OrderStatus
from app.models.signal import SignalAction, TradingViewSignal
from app.risk.manager import RiskManager
from app.storage.repository import finalize_signal_log, log_trade, try_reserve_signal

logger = logging.getLogger(__name__)


def _side_and_quantity(
    signal: TradingViewSignal, broker_name: str, broker: BrokerAdapter, mapping: dict
) -> tuple[OrderSide, float] | None:
    """Translate a signal action into a concrete (side, quantity) for one broker.
    Returns None if there's nothing to do (e.g. close requested but no position exists).
    """
    if signal.action == SignalAction.BUY:
        return OrderSide.BUY, signal.quantity
    if signal.action == SignalAction.SELL:
        return OrderSide.SELL, signal.quantity
    if signal.action == SignalAction.CLOSE_LONG:
        return OrderSide.SELL, signal.quantity
    if signal.action == SignalAction.CLOSE_SHORT:
        return OrderSide.BUY, signal.quantity

    if signal.action == SignalAction.CLOSE_ALL:
        try:
            qty = broker.get_signed_position_qty(mapping)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch positions from %s to resolve close_all: %s", broker_name, exc)
            return None
        if not qty:
            return None
        return (OrderSide.SELL, abs(qty)) if qty > 0 else (OrderSide.BUY, abs(qty))

    return None


def process_signal(
    signal: TradingViewSignal,
    settings: Settings,
    brokers: dict[str, BrokerAdapter],
    symbol_map: dict,
    session: Session,
    risk_manager: RiskManager,
) -> list[OrderResult]:
    reservation = try_reserve_signal(
        session,
        signal_id=signal.signal_id,
        strategy=signal.strategy,
        symbol=signal.symbol,
        action=signal.action.value,
        quantity=signal.quantity,
    )
    if reservation is None:
        logger.info("Duplicate signal_id=%s ignored", signal.signal_id)
        return [OrderResult(broker="*", status=OrderStatus.REJECTED, message="Duplicate signal_id, already processed")]

    risk_result = risk_manager.check(signal, session, brokers)
    if not risk_result.allowed:
        logger.warning("Signal %s rejected by risk manager: %s", signal.signal_id, risk_result.reason)
        finalize_signal_log(session, reservation, OrderStatus.REJECTED.value, f"Risk check failed: {risk_result.reason}")
        return [OrderResult(broker="*", status=OrderStatus.REJECTED, message=risk_result.reason)]

    symbol_mapping = symbol_map.get(signal.symbol, {})
    if signal.broker == "any":
        target_broker_names = [name for name in brokers if name in symbol_mapping]
    else:
        target_broker_names = [signal.broker] if signal.broker in brokers else []

    if not target_broker_names:
        message = f"No enabled broker has a symbol mapping for {signal.symbol} (requested broker={signal.broker!r})"
        logger.warning(message)
        finalize_signal_log(session, reservation, OrderStatus.REJECTED.value, message)
        return [OrderResult(broker="*", status=OrderStatus.REJECTED, message=message)]

    results: list[OrderResult] = []
    for broker_name in target_broker_names:
        broker = brokers[broker_name]
        mapping = symbol_mapping.get(broker_name, {})

        resolved = _side_and_quantity(signal, broker_name, broker, mapping)
        if resolved is None:
            result = OrderResult(broker=broker_name, status=OrderStatus.REJECTED, message="Nothing to close / no position found")
            results.append(result)
            log_trade(
                session,
                signal_id=f"{signal.signal_id}:{broker_name}",
                strategy=signal.strategy,
                symbol=signal.symbol,
                action=signal.action.value,
                side=None,
                quantity=signal.quantity,
                broker=broker_name,
                status=result.status.value,
                message=result.message,
            )
            continue

        side, quantity = resolved
        order_request = OrderRequest(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            action=signal.action,
            side=side,
            quantity=quantity,
            order_type=signal.order_type,
            product_type=signal.product_type,
            price=signal.price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            broker_symbol=symbol_mapping,
        )

        if settings.trading_mode == "paper":
            result = OrderResult(
                broker=broker_name,
                status=OrderStatus.SIMULATED,
                message=f"PAPER MODE: would have placed {side.value} {quantity} {signal.symbol} on {broker_name}",
            )
        else:
            try:
                result = broker.place_order(order_request)
            except Exception as exc:  # noqa: BLE001 - never let a broker bug crash the whole batch
                logger.exception("Broker %s raised while placing order for signal %s", broker_name, signal.signal_id)
                result = OrderResult(broker=broker_name, status=OrderStatus.ERROR, message=str(exc))

        results.append(result)
        log_trade(
            session,
            signal_id=f"{signal.signal_id}:{broker_name}",
            strategy=signal.strategy,
            symbol=signal.symbol,
            action=signal.action.value,
            side=side.value,
            quantity=quantity,
            broker=broker_name,
            status=result.status.value,
            message=result.message,
            broker_order_id=result.broker_order_id,
        )

    finalize_signal_log(
        session,
        reservation,
        status="dispatched",
        message=f"Fanned out to {len(target_broker_names)} broker(s): {', '.join(target_broker_names)}",
    )

    return results
