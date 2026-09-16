import pytest
from pydantic import ValidationError

from app.models.signal import OrderType, SignalAction, TradingViewSignal


def _base_payload(**overrides):
    payload = {
        "passphrase": "x",
        "signal_id": "abc-1",
        "strategy": "ema_cross",
        "symbol": "eurusd",
        "action": "buy",
        "quantity": 0.1,
    }
    payload.update(overrides)
    return payload


def test_symbol_is_uppercased():
    signal = TradingViewSignal(**_base_payload())
    assert signal.symbol == "EURUSD"


def test_defaults():
    signal = TradingViewSignal(**_base_payload())
    assert signal.order_type == OrderType.MARKET
    assert signal.action == SignalAction.BUY


def test_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        TradingViewSignal(**_base_payload(quantity=0))


def test_limit_order_requires_price():
    with pytest.raises(ValidationError):
        TradingViewSignal(**_base_payload(order_type="limit"))


def test_limit_order_with_price_ok():
    signal = TradingViewSignal(**_base_payload(order_type="limit", price=1.085))
    assert signal.price == 1.085


def test_invalid_action_rejected():
    with pytest.raises(ValidationError):
        TradingViewSignal(**_base_payload(action="yolo"))
