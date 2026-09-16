from abc import ABC, abstractmethod

from app.models.order import OrderRequest, OrderResult


class BrokerAdapter(ABC):
    """Common interface a broker integration implements. Async throughout -
    the whole request path (webhook -> risk -> broker) is async so one
    slow/offline tenant's order never blocks another tenant's request.
    """

    name: str = "base"

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    async def get_open_positions(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def get_account_summary(self) -> dict:
        raise NotImplementedError
