from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.models.signal import OrderType, ProductType, SignalAction


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SIMULATED = "simulated"
    ERROR = "error"


@dataclass
class OrderRequest:
    """Broker-agnostic order request built from a validated TradingViewSignal."""

    signal_id: str
    symbol: str
    action: SignalAction
    side: Optional[OrderSide]
    quantity: float
    order_type: OrderType
    product_type: ProductType
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    broker_symbol: dict = field(default_factory=dict)


@dataclass
class OrderResult:
    broker: str
    status: OrderStatus
    broker_order_id: Optional[str] = None
    message: str = ""
    raw_response: Optional[Any] = None
