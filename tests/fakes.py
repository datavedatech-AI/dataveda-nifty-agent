from app.brokers.base import BrokerAdapter
from app.models.order import OrderRequest, OrderResult, OrderStatus


class FakeBroker(BrokerAdapter):
    """In-memory broker double used across tests - no network calls."""

    def __init__(self, name: str = "fake", positions: list[dict] | None = None):
        self.name = name
        self._positions = positions or []
        self.placed_orders: list[OrderRequest] = []
        self.next_result: OrderResult | None = None

    def place_order(self, order: OrderRequest) -> OrderResult:
        self.placed_orders.append(order)
        if self.next_result is not None:
            return self.next_result
        return OrderResult(broker=self.name, status=OrderStatus.ACCEPTED, broker_order_id="FAKE-1", message="ok")

    def get_open_positions(self) -> list[dict]:
        return self._positions

    def get_account_summary(self) -> dict:
        return {"balance": 100000}

    def get_signed_position_qty(self, mapping: dict) -> float | None:
        # Test double convention: positions carry a plain "qty" field keyed
        # by the mapping's "id", signed (positive=long, negative=short).
        wanted_id = mapping.get("id")
        for pos in self._positions:
            if pos.get("id") == wanted_id:
                return pos.get("qty")
        return None
