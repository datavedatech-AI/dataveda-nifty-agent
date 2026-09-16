from app.models.signal import TradingViewSignal
from app.risk.manager import check_risk
from app.storage.repository import create_tenant, log_trade


def make_signal(**overrides) -> TradingViewSignal:
    base = dict(
        passphrase="secret",
        signal_id="sig-1",
        strategy="s1",
        symbol="EURUSD",
        action="buy",
        quantity=0.5,
    )
    base.update(overrides)
    return TradingViewSignal(**base)


async def _tenant(db_session, **risk_overrides):
    tenant, _ = await create_tenant(db_session, f"user-{risk_overrides}@example.com")
    # Reassign (don't mutate in place) - SQLAlchemy only tracks JSON columns
    # as dirty on reassignment, not on mutating the dict it already holds.
    tenant.risk_settings = {**tenant.risk_settings, **risk_overrides}
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant


async def test_allows_when_everything_ok(db_session):
    tenant = await _tenant(db_session, max_qty_per_order=10)
    result = await check_risk(tenant, make_signal(), db_session)
    assert result.allowed is True


async def test_kill_switch_blocks(db_session):
    tenant = await _tenant(db_session)
    tenant.kill_switch_engaged = True
    tenant.kill_switch_reason = "manual halt"
    await db_session.commit()

    result = await check_risk(tenant, make_signal(), db_session)
    assert result.allowed is False
    assert "manual halt" in result.reason


async def test_symbol_allowlist_blocks(db_session):
    tenant = await _tenant(db_session, allowed_symbols=["GBPUSD"])
    result = await check_risk(tenant, make_signal(symbol="EURUSD"), db_session)
    assert result.allowed is False
    assert "not in this account's allowed_symbols" in result.reason


async def test_max_qty_blocks(db_session):
    tenant = await _tenant(db_session, max_qty_per_order=0.1)
    result = await check_risk(tenant, make_signal(quantity=0.5), db_session)
    assert result.allowed is False
    assert "exceeds max_qty_per_order" in result.reason


async def test_daily_loss_circuit_breaker_blocks(db_session):
    tenant = await _tenant(db_session, max_daily_loss=500)
    entry = await log_trade(
        db_session,
        tenant_id=tenant.id,
        signal_id="loss-1",
        strategy="s1",
        symbol="EURUSD",
        action="sell",
        side="sell",
        quantity=0.5,
        status="accepted",
    )
    entry.realized_pnl = -600
    await db_session.commit()

    result = await check_risk(tenant, make_signal(), db_session)
    assert result.allowed is False
    assert "Daily loss circuit breaker" in result.reason


async def test_daily_loss_only_counts_this_tenant(db_session):
    tenant_a = await _tenant(db_session, max_daily_loss=500)
    tenant_b, _ = await create_tenant(db_session, "other@example.com")

    entry = await log_trade(
        db_session,
        tenant_id=tenant_b.id,
        signal_id="loss-b",
        strategy="s1",
        symbol="EURUSD",
        action="sell",
        side="sell",
        quantity=0.5,
        status="accepted",
    )
    entry.realized_pnl = -9000
    await db_session.commit()

    result = await check_risk(tenant_a, make_signal(), db_session)
    assert result.allowed is True
