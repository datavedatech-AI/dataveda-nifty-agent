from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import TradingViewSignal
from app.storage.models import Tenant
from app.storage.repository import get_today_realized_pnl


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str = ""


async def check_risk(tenant: Tenant, signal: TradingViewSignal, session: AsyncSession) -> RiskCheckResult:
    """Guardrails applied to every signal before an order is sent, in order
    (first failure wins): kill switch, symbol allowlist, max qty per order,
    daily realized-loss circuit breaker.

    Deliberately does NOT check live open-position count here - that would
    mean a broker round trip (over the WebSocket relay) on every single
    signal before we even know if it'll pass, which is the kind of added
    latency this product is trying to avoid. Open-position limits can be
    enforced by the bridge agent itself in a later pass if needed.
    """
    if tenant.kill_switch_engaged:
        return RiskCheckResult(False, f"Kill switch engaged: {tenant.kill_switch_reason or 'no reason given'}")

    settings = tenant.risk_settings or {}

    allowed_symbols = [s.strip().upper() for s in settings.get("allowed_symbols", []) if s.strip()]
    if allowed_symbols and signal.symbol not in allowed_symbols:
        return RiskCheckResult(False, f"Symbol {signal.symbol} is not in this account's allowed_symbols")

    max_qty = settings.get("max_qty_per_order")
    if max_qty and signal.quantity > max_qty:
        return RiskCheckResult(False, f"Quantity {signal.quantity} exceeds max_qty_per_order={max_qty}")

    max_daily_loss = settings.get("max_daily_loss")
    if max_daily_loss:
        today_pnl = await get_today_realized_pnl(session, tenant.id)
        if today_pnl <= -abs(max_daily_loss):
            return RiskCheckResult(
                False, f"Daily loss circuit breaker tripped: realized PnL today {today_pnl:.2f} <= -{max_daily_loss}"
            )

    return RiskCheckResult(True)
