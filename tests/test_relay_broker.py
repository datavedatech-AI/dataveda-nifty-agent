import asyncio

from app.models.order import OrderRequest, OrderSide, OrderStatus
from app.models.signal import OrderType, SignalAction
from app.ws.manager import ConnectionManager
from app.ws.relay_broker import WSRelayBroker
from tests.test_ws_manager import FakeWebSocket


def make_order(**overrides) -> OrderRequest:
    base = dict(
        signal_id="sig-1",
        symbol="EURUSD",
        action=SignalAction.BUY,
        side=OrderSide.BUY,
        quantity=0.5,
        order_type=OrderType.MARKET,
        mt5_symbol="EURUSD",
    )
    base.update(overrides)
    return OrderRequest(**base)


async def test_place_order_success_via_relay():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect("tenant-1", ws)
    broker = WSRelayBroker(manager, "tenant-1", timeout=2.0)

    async def respond():
        await asyncio.sleep(0.01)
        sent = ws.sent[0]
        manager.resolve(sent["request_id"], {"status": "accepted", "order_id": "999", "message": "filled"})

    asyncio.create_task(respond())
    result = await broker.place_order(make_order())

    assert result.status == OrderStatus.ACCEPTED
    assert result.broker_order_id == "999"
    assert ws.sent[0]["symbol"] == "EURUSD"
    assert ws.sent[0]["side"] == "buy"


async def test_place_order_when_agent_offline():
    manager = ConnectionManager()
    broker = WSRelayBroker(manager, "tenant-without-agent", timeout=1.0)

    result = await broker.place_order(make_order())

    assert result.status == OrderStatus.REJECTED
    assert "No bridge agent connected" in result.message


async def test_place_order_times_out():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect("tenant-1", ws)
    broker = WSRelayBroker(manager, "tenant-1", timeout=0.05)

    result = await broker.place_order(make_order())

    assert result.status == OrderStatus.ERROR
    assert "did not respond in time" in result.message


async def test_place_order_without_mt5_symbol_is_rejected_locally():
    manager = ConnectionManager()
    broker = WSRelayBroker(manager, "tenant-1", timeout=1.0)

    result = await broker.place_order(make_order(mt5_symbol=""))

    assert result.status == OrderStatus.REJECTED
    assert "No MT5 symbol mapped" in result.message
