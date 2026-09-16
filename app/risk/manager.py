import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter
from app.config import Settings
from app.models.signal import TradingViewSignal
from app.storage.repository import get_kill_switch, get_today_realized_pnl

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str = ""


class RiskManager:
    """Guardrails applied to every signal before an order is sent.

    Checks, in order (first failure wins):
      1. Global kill switch (env var OR runtime DB flag)
      2. Symbol allowlist
      3. Max quantity per single order
      4. Daily realized-loss circuit breaker
      5. Max simultaneously open positions (best-effort, per broker)
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def check(self, signal: TradingViewSignal, session: Session, brokers: dict[str, BrokerAdapter]) -> RiskCheckResult:
        if self.settings.kill_switch:
            return RiskCheckResult(False, "Kill switch engaged via KILL_SWITCH env var")

        db_kill_switch = get_kill_switch(session)
        if db_kill_switch.engaged:
            return RiskCheckResult(False, f"Kill switch engaged at runtime: {db_kill_switch.reason or 'no reason given'}")

        allowed_symbols = self.settings.risk_allowed_symbols_list
        if allowed_symbols and signal.symbol not in allowed_symbols:
            return RiskCheckResult(False, f"Symbol {signal.symbol} not in RISK_ALLOWED_SYMBOLS")

        if signal.quantity > self.settings.risk_max_qty_per_order:
            return RiskCheckResult(
                False,
                f"Quantity {signal.quantity} exceeds RISK_MAX_QTY_PER_ORDER={self.settings.risk_max_qty_per_order}",
            )

        today_pnl = get_today_realized_pnl(session)
        if today_pnl <= -abs(self.settings.risk_max_daily_loss):
            return RiskCheckResult(
                False,
                f"Daily loss circuit breaker tripped: realized PnL today {today_pnl:.2f} "
                f"<= -{self.settings.risk_max_daily_loss}",
            )

        for name, broker in brokers.items():
            try:
                open_positions = broker.get_open_positions()
            except Exception as exc:  # noqa: BLE001 - best-effort risk check, must not crash the pipeline
                logger.warning("Could not fetch open positions from %s for risk check: %s", name, exc)
                continue
            if len(open_positions) >= self.settings.risk_max_open_positions:
                return RiskCheckResult(
                    False,
                    f"{name}: {len(open_positions)} open positions >= "
                    f"RISK_MAX_OPEN_POSITIONS={self.settings.risk_max_open_positions}",
                )

        return RiskCheckResult(True)
