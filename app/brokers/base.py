from abc import ABC, abstractmethod

from app.models.order import OrderRequest, OrderResult


class BrokerAdapter(ABC):
    """Common interface every broker integration must implement.

    Adding a new broker (e.g. Zerodha, Interactive Brokers, Binance) means
    writing one class that implements these four methods and registering it
    in app/brokers/registry.py - nothing else in the app needs to change.
    """

    name: str = "base"

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        """Send a new order (or a closing order) to the broker."""
        raise NotImplementedError

    @abstractmethod
    def get_open_positions(self) -> list[dict]:
        """Return currently open positions in a broker-agnostic list of dicts."""
        raise NotImplementedError

    @abstractmethod
    def get_account_summary(self) -> dict:
        """Return balance / margin / equity info, used for logging and risk checks."""
        raise NotImplementedError

    def get_signed_position_qty(self, mapping: dict) -> float | None:
        """Return this broker's currently open signed quantity for the symbol
        described by `mapping` (positive = long, negative = short), or None
        if there's no open position or it can't be determined. Used to
        resolve "close_all" signals. Each adapter knows the shape its own
        get_open_positions() returns, so this stays broker-specific instead
        of being parsed centrally by the order router.
        """
        return None

    def supports_symbol(self, symbol: str, symbol_map: dict) -> bool:
        return self.name in symbol_map.get(symbol, {})
