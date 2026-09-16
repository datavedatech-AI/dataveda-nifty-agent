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


class ProductType(str, Enum):
    INTRADAY = "intraday"
    DELIVERY = "delivery"
    MARGIN = "margin"


class TradingViewSignal(BaseModel):
    """Shape of the JSON body a TradingView alert() posts to our webhook.

    Pine Script alert message example (see pine/example_strategy_alert.md):
      {
        "passphrase": "{{strategy.order.alert_message}}",
        "signal_id": "{{ticker}}-{{time}}-{{strategy.order.id}}",
        "strategy": "my_ema_cross",
        "symbol": "NIFTY",
        "action": "buy",
        "quantity": 75,
        "order_type": "market",
        "product_type": "intraday",
        "price": 0,
        "stop_loss": 0,
        "take_profit": 0,
        "broker": "any"
      }
    """

    passphrase: str
    signal_id: str = Field(..., description="Unique id from TradingView used to dedupe retried alerts")
    strategy: str
    symbol: str
    action: SignalAction
    quantity: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    product_type: ProductType = ProductType.INTRADAY
    price: Optional[float] = Field(default=None, description="Required for limit orders")
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    broker: str = Field(default="any", description="'any' (all enabled brokers), or a specific broker name")

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _price_required_for_limit(self) -> "TradingViewSignal":
        if self.order_type == OrderType.LIMIT and (self.price is None or self.price <= 0):
            raise ValueError("price must be a positive number for limit orders")
        return self
