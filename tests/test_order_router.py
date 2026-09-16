from app.models.order import OrderStatus
from app.models.signal import TradingViewSignal
from app.services.order_router import process_signal
from app.storage.repository import create_tenant, grant_subscription
from tests.fakes import FakeBroker


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


async def _tenant_with_symbol_map(db_session, symbol_map=None, email="trader@example.com"):
    tenant, _ = await create_tenant(db_session, email)
    tenant.symbol_map = symbol_map if symbol_map is not None else {"EURUSD": "EURUSD"}
    await db_session.commit()
    await db_session.refresh(tenant)
    tenant = await grant_subscription(db_session, tenant, days=30)
    return tenant


async def test_places_order_via_broker(db_session):
    tenant = await _tenant_with_symbol_map(db_session)
    broker = FakeBroker()

    result = await process_signal(make_signal(), tenant, db_session, broker)

    assert result.status == OrderStatus.ACCEPTED
    assert len(broker.placed_orders) == 1
    assert broker.placed_orders[0].quantity == 0.5
    assert broker.placed_orders[0].mt5_symbol == "EURUSD"


async def test_duplicate_signal_id_is_rejected(db_session):
    tenant = await _tenant_with_symbol_map(db_session)
    broker = FakeBroker()

    await process_signal(make_signal(signal_id="dup-1"), tenant, db_session, broker)
    result = await process_signal(make_signal(signal_id="dup-1"), tenant, db_session, broker)

    assert len(broker.placed_orders) == 1  # second call never reached the broker
    assert result.status == OrderStatus.REJECTED
    assert "Duplicate" in result.message


async def test_same_signal_id_different_tenants_both_execute(db_session):
    tenant_a = await _tenant_with_symbol_map(db_session)
    tenant_b = await _tenant_with_symbol_map(db_session, email="other@example.com")

    broker_a, broker_b = FakeBroker(), FakeBroker()
    result_a = await process_signal(make_signal(signal_id="shared-id"), tenant_a, db_session, broker_a)
    result_b = await process_signal(make_signal(signal_id="shared-id"), tenant_b, db_session, broker_b)

    assert result_a.status == OrderStatus.ACCEPTED
    assert result_b.status == OrderStatus.ACCEPTED


async def test_unmapped_symbol_is_rejected(db_session):
    tenant = await _tenant_with_symbol_map(db_session, symbol_map={})
    broker = FakeBroker()

    result = await process_signal(make_signal(), tenant, db_session, broker)

    assert result.status == OrderStatus.REJECTED
    assert "No MT5 symbol mapped" in result.message
    assert broker.placed_orders == []


async def test_close_all_is_rejected_with_explanation(db_session):
    tenant = await _tenant_with_symbol_map(db_session)
    broker = FakeBroker()

    result = await process_signal(make_signal(action="close_all", signal_id="close-1"), tenant, db_session, broker)

    assert result.status == OrderStatus.REJECTED
    assert "close_all is not supported" in result.message
    assert broker.placed_orders == []


async def test_close_long_places_opposite_side_order(db_session):
    tenant = await _tenant_with_symbol_map(db_session)
    broker = FakeBroker()

    result = await process_signal(make_signal(action="close_long", signal_id="close-2"), tenant, db_session, broker)

    assert result.status == OrderStatus.ACCEPTED
    assert broker.placed_orders[0].side.value == "sell"


async def test_kill_switch_blocks_before_broker_call(db_session):
    tenant = await _tenant_with_symbol_map(db_session)
    tenant.kill_switch_engaged = True
    await db_session.commit()
    broker = FakeBroker()

    result = await process_signal(make_signal(), tenant, db_session, broker)

    assert result.status == OrderStatus.REJECTED
    assert broker.placed_orders == []


async def test_no_subscription_blocks_before_broker_call(db_session):
    tenant, _ = await create_tenant(db_session, "unpaid@example.com")
    tenant.symbol_map = {"EURUSD": "EURUSD"}
    await db_session.commit()
    broker = FakeBroker()

    result = await process_signal(make_signal(), tenant, db_session, broker)

    assert result.status == OrderStatus.REJECTED
    assert "No active subscription" in result.message
    assert broker.placed_orders == []
