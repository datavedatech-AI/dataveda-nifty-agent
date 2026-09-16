import asyncio
import logging
from dataclasses import dataclass

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


class BridgeOfflineError(Exception):
    """No bridge agent is currently connected for this tenant."""


class BridgeTimeoutError(Exception):
    """The tenant's bridge agent didn't reply before the deadline."""


@dataclass
class _Pending:
    future: "asyncio.Future[dict]"


class ConnectionManager:
    """Tracks each tenant's live WebSocket connection from their MT5 bridge
    agent, and correlates an outbound order request with the agent's async
    reply.

    This is the low-latency path: the agent holds one persistent outbound
    connection open, so placing an order is a single JSON frame down an
    already-established socket - no per-order handshake, no inbound port
    on the customer's machine.

    In-memory and single-process. Fine for one server instance. Scaling to
    multiple instances needs a shared layer (e.g. Redis pub/sub) so an order
    for a tenant connected to instance A can be routed from instance B -
    noted here as a known next step, not solved by this class.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, _Pending] = {}

    async def connect(self, tenant_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[tenant_id] = websocket
        logger.info("Bridge agent connected for tenant %s", tenant_id)

    def disconnect(self, tenant_id: str) -> None:
        self._connections.pop(tenant_id, None)
        logger.info("Bridge agent disconnected for tenant %s", tenant_id)

    def is_connected(self, tenant_id: str) -> bool:
        return tenant_id in self._connections

    async def send_request(self, tenant_id: str, message: dict, timeout: float) -> dict:
        websocket = self._connections.get(tenant_id)
        if websocket is None:
            raise BridgeOfflineError(f"No bridge agent connected for tenant {tenant_id}")

        request_id = message["request_id"]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = _Pending(future=future)
        try:
            await websocket.send_json(message)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise BridgeTimeoutError(f"Bridge agent for tenant {tenant_id} did not respond in time") from exc
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, result: dict) -> None:
        pending = self._pending.get(request_id)
        if pending is not None and not pending.future.done():
            pending.future.set_result(result)
