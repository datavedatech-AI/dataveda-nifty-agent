from app.brokers.base import BrokerAdapter
from app.models.order import OrderRequest, OrderResult, OrderStatus


class FakeBroker(BrokerAdapter):
    """In-memory async broker double used across tests - no network/WS calls."""

    name = "mt5"

    def __init__(self, positions: list[dict] | None = None):
        self._positions = positions or []
        self.placed_orders: list[OrderRequest] = []
        self.next_result: OrderResult | None = None

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self.placed_orders.append(order)
        if self.next_result is not None:
            return self.next_result
        return OrderResult(broker=self.name, status=OrderStatus.ACCEPTED, broker_order_id="FAKE-1", message="ok")

    async def get_open_positions(self) -> list[dict]:
        return self._positions

    async def get_account_summary(self) -> dict:
        return {"balance": 100000}
