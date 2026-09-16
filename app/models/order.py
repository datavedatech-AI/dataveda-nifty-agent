from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from app.models.signal import OrderType, SignalAction


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass
class OrderRequest:
    """Broker-agnostic order request built from a validated TradingViewSignal."""

    signal_id: str
    symbol: str
    action: SignalAction
    side: OrderSide
    quantity: float
    order_type: OrderType
    mt5_symbol: str
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class OrderResult:
    broker: str
    status: OrderStatus
    broker_order_id: Optional[str] = None
    message: str = ""
    raw_response: Optional[Any] = None
