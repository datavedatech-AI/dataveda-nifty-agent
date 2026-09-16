import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.storage.models import KillSwitchState, TradeLog


def signal_already_processed(session: Session, signal_id: str) -> bool:
    stmt = select(TradeLog.id).where(TradeLog.signal_id == signal_id)
    return session.execute(stmt).first() is not None


def try_reserve_signal(
    session: Session, signal_id: str, strategy: str, symbol: str, action: str, quantity: float
) -> TradeLog | None:
    """Atomically claim a signal_id via the TradeLog.signal_id unique constraint.

    Returns the reserved TradeLog row if this call successfully claimed it
    (safe to proceed), or None if another concurrent request already
    claimed it (a duplicate webhook delivery, e.g. a TradingView retry).
    This must happen BEFORE any broker call so two near-simultaneous
    deliveries of the same alert can never both place an order.
    """
    entry = TradeLog(
        signal_id=signal_id,
        strategy=strategy,
        symbol=symbol,
        action=action,
        side=None,
        quantity=quantity,
        broker="*",
        status="received",
        message="",
    )
    session.add(entry)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    session.refresh(entry)
    return entry


def finalize_signal_log(session: Session, entry: TradeLog, status: str, message: str) -> None:
    entry.status = status
    entry.message = message
    session.commit()


def log_trade(
    session: Session,
    *,
    signal_id: str,
    strategy: str,
    symbol: str,
    action: str,
    side: str | None,
    quantity: float,
    broker: str,
    status: str,
    message: str = "",
    broker_order_id: str | None = None,
) -> TradeLog:
    entry = TradeLog(
        signal_id=signal_id,
        strategy=strategy,
        symbol=symbol,
        action=action,
        side=side,
        quantity=quantity,
        broker=broker,
        status=status,
        message=message,
        broker_order_id=broker_order_id,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def record_realized_pnl(session: Session, trade_log_id: int, pnl: float) -> None:
    entry = session.get(TradeLog, trade_log_id)
    if entry is not None:
        entry.realized_pnl = pnl
        session.commit()


def get_today_realized_pnl(session: Session) -> float:
    start_of_day = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(TradeLog.realized_pnl), 0.0)).where(TradeLog.created_at >= start_of_day)
    return session.execute(stmt).scalar_one()


def get_kill_switch(session: Session) -> KillSwitchState:
    state = session.get(KillSwitchState, 1)
    if state is None:
        state = KillSwitchState(id=1, engaged=False, reason="")
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def set_kill_switch(session: Session, engaged: bool, reason: str = "") -> KillSwitchState:
    state = get_kill_switch(session)
    state.engaged = engaged
    state.reason = reason
    state.updated_at = dt.datetime.now(dt.timezone.utc)
    session.commit()
    session.refresh(state)
    return state
