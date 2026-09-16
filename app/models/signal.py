from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"
    CLOSE_ALL = "close_all"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TradingViewSignal(BaseModel):
    """Shape of the JSON body a TradingView alert() posts to
    /webhook/tradingview/{webhook_id}.

    Example:
      {
        "passphrase": "...",
        "signal_id": "{{ticker}}-{{interval}}-{{time}}",
        "strategy": "my_ema_cross",
        "symbol": "EURUSD",
        "action": "buy",
        "quantity": 0.1,
        "order_type": "market"
      }
    """

    passphrase: str
    signal_id: str = Field(..., description="Unique id from TradingView used to dedupe retried alerts")
    strategy: str
    symbol: str
    action: SignalAction
    quantity: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    price: Optional[float] = Field(default=None, description="Required for limit orders")
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _price_required_for_limit(self) -> "TradingViewSignal":
        if self.order_type == OrderType.LIMIT and (self.price is None or self.price <= 0):
            raise ValueError("price must be a positive number for limit orders")
        return self
