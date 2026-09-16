import datetime as dt
import hashlib
import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import Tenant, TradeLog


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _generate_id(prefix: str, nbytes: int = 16) -> str:
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


def default_risk_settings() -> dict:
    return {
        "max_qty_per_order": 1.0,
        "max_daily_loss": 1000.0,
        "allowed_symbols": [],
    }


async def create_tenant(session: AsyncSession, email: str) -> tuple[Tenant, dict] | None:
    """Create a tenant and return (tenant, {plaintext secrets shown once}).
    Returns None if the email is already registered.
    """
    api_key = _generate_id("sk_live", 24)
    webhook_id = secrets.token_urlsafe(12)
    webhook_passphrase = secrets.token_urlsafe(18)
    agent_token = _generate_id("agent", 24)

    tenant = Tenant(
        id=_generate_id("ten", 12),
        email=email,
        api_key_hash=hash_secret(api_key),
        webhook_id=webhook_id,
        webhook_passphrase_hash=hash_secret(webhook_passphrase),
        agent_token_hash=hash_secret(agent_token),
        symbol_map={},
        risk_settings=default_risk_settings(),
    )
    session.add(tenant)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    await session.refresh(tenant)

    return tenant, {
        "api_key": api_key,
        "webhook_id": webhook_id,
        "webhook_passphrase": webhook_passphrase,
        "agent_token": agent_token,
    }


async def get_tenant_by_api_key(session: AsyncSession, api_key: str) -> Tenant | None:
    stmt = select(Tenant).where(Tenant.api_key_hash == hash_secret(api_key))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_tenant_by_webhook_id(session: AsyncSession, webhook_id: str) -> Tenant | None:
    stmt = select(Tenant).where(Tenant.webhook_id == webhook_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_tenant_by_agent_token(session: AsyncSession, agent_token: str) -> Tenant | None:
    stmt = select(Tenant).where(Tenant.agent_token_hash == hash_secret(agent_token))
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_kill_switch(session: AsyncSession, tenant: Tenant, engaged: bool, reason: str = "") -> Tenant:
    tenant.kill_switch_engaged = engaged
    tenant.kill_switch_reason = reason
    await session.commit()
    await session.refresh(tenant)
    return tenant


async def update_symbol_map(session: AsyncSession, tenant: Tenant, symbol_map: dict) -> Tenant:
    tenant.symbol_map = symbol_map
    await session.commit()
    await session.refresh(tenant)
    return tenant


async def update_risk_settings(session: AsyncSession, tenant: Tenant, risk_settings: dict) -> Tenant:
    tenant.risk_settings = risk_settings
    await session.commit()
    await session.refresh(tenant)
    return tenant


async def try_reserve_signal(
    session: AsyncSession, tenant_id: str, signal_id: str, strategy: str, symbol: str, action: str, quantity: float
) -> TradeLog | None:
    """Atomically claim (tenant_id, signal_id) via a unique constraint.

    Returns the reserved row if this call claimed it, or None if another
    concurrent request already claimed it (e.g. a retried TradingView
    webhook delivery). Must happen BEFORE any broker call.
    """
    entry = TradeLog(
        tenant_id=tenant_id,
        signal_id=signal_id,
        strategy=strategy,
        symbol=symbol,
        action=action,
        side=None,
        quantity=quantity,
        status="received",
        message="",
    )
    session.add(entry)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    await session.refresh(entry)
    return entry


async def finalize_signal_log(session: AsyncSession, entry: TradeLog, status: str, message: str) -> None:
    entry.status = status
    entry.message = message
    await session.commit()


async def log_trade(
    session: AsyncSession,
    *,
    tenant_id: str,
    signal_id: str,
    strategy: str,
    symbol: str,
    action: str,
    side: str | None,
    quantity: float,
    status: str,
    message: str = "",
    broker_order_id: str | None = None,
) -> TradeLog:
    entry = TradeLog(
        tenant_id=tenant_id,
        signal_id=signal_id,
        strategy=strategy,
        symbol=symbol,
        action=action,
        side=side,
        quantity=quantity,
        status=status,
        message=message,
        broker_order_id=broker_order_id,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def get_today_realized_pnl(session: AsyncSession, tenant_id: str) -> float:
    start_of_day = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(TradeLog.realized_pnl), 0.0)).where(
        TradeLog.tenant_id == tenant_id, TradeLog.created_at >= start_of_day
    )
    return (await session.execute(stmt)).scalar_one()
