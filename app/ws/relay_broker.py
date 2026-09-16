import uuid

from app.brokers.base import BrokerAdapter
from app.models.order import OrderRequest, OrderResult, OrderStatus
from app.ws.manager import BridgeOfflineError, BridgeTimeoutError, ConnectionManager

_STATUS_MAP = {
    "accepted": OrderStatus.ACCEPTED,
    "rejected": OrderStatus.REJECTED,
    "error": OrderStatus.ERROR,
}


class WSRelayBroker(BrokerAdapter):
    """Places MT5 orders by pushing them over a tenant's live WebSocket
    connection to their locally-run bridge agent, and awaiting the reply.
    This is the hosted product's only broker path - no per-order HTTP
    handshake, no inbound port needed on the customer's machine.
    """

    name = "mt5"

    def __init__(self, manager: ConnectionManager, tenant_id: str, timeout: float = 8.0):
        self.manager = manager
        self.tenant_id = tenant_id
        self.timeout = timeout

    async def place_order(self, order: OrderRequest) -> OrderResult:
        if not order.mt5_symbol:
            return OrderResult(broker=self.name, status=OrderStatus.REJECTED, message=f"No MT5 symbol mapped for {order.symbol}")

        message = {
            "type": "place_order",
            "request_id": str(uuid.uuid4()),
            "symbol": order.mt5_symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "order_type": order.order_type.value,
            "price": order.price,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
        }

        try:
            result = await self.manager.send_request(self.tenant_id, message, timeout=self.timeout)
        except BridgeOfflineError as exc:
            return OrderResult(broker=self.name, status=OrderStatus.REJECTED, message=str(exc))
        except BridgeTimeoutError as exc:
            return OrderResult(broker=self.name, status=OrderStatus.ERROR, message=str(exc))

        return OrderResult(
            broker=self.name,
            status=_STATUS_MAP.get(result.get("status"), OrderStatus.ERROR),
            broker_order_id=result.get("order_id"),
            message=result.get("message", ""),
            raw_response=result.get("raw"),
        )

    async def get_open_positions(self) -> list[dict]:
        # Not fetched live over the relay in this MVP - each round trip adds
        # latency to the hot path. See risk manager / close_all limitations.
        return []

    async def get_account_summary(self) -> dict:
        return {}
