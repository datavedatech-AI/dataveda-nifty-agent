import asyncio

import pytest

from app.ws.manager import BridgeOfflineError, BridgeTimeoutError, ConnectionManager


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent: list[dict] = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, data: dict):
        self.sent.append(data)


async def test_send_request_without_connection_raises_offline():
    manager = ConnectionManager()
    with pytest.raises(BridgeOfflineError):
        await manager.send_request("tenant-1", {"request_id": "r1"}, timeout=1.0)


async def test_connect_then_send_and_resolve_round_trip():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect("tenant-1", ws)
    assert ws.accepted is True
    assert manager.is_connected("tenant-1") is True

    async def resolve_soon():
        await asyncio.sleep(0.01)
        manager.resolve("r1", {"status": "accepted", "order_id": "123"})

    asyncio.create_task(resolve_soon())
    result = await manager.send_request("tenant-1", {"request_id": "r1", "type": "place_order"}, timeout=2.0)

    assert result == {"status": "accepted", "order_id": "123"}
    assert ws.sent == [{"request_id": "r1", "type": "place_order"}]


async def test_send_request_times_out_if_never_resolved():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect("tenant-1", ws)

    with pytest.raises(BridgeTimeoutError):
        await manager.send_request("tenant-1", {"request_id": "r-timeout"}, timeout=0.05)


async def test_disconnect_removes_connection():
    manager = ConnectionManager()
    ws = FakeWebSocket()
    await manager.connect("tenant-1", ws)
    manager.disconnect("tenant-1")

    assert manager.is_connected("tenant-1") is False
    with pytest.raises(BridgeOfflineError):
        await manager.send_request("tenant-1", {"request_id": "r2"}, timeout=1.0)


async def test_resolve_for_unknown_request_id_is_ignored():
    manager = ConnectionManager()
    # Should not raise even though nothing is pending for this id.
    manager.resolve("no-such-request", {"status": "accepted"})
