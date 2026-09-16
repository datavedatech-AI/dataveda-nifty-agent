import json

import httpx

from app.brokers.dhan import DhanAdapter
from app.models.order import OrderRequest, OrderSide, OrderStatus
from app.models.signal import OrderType, ProductType, SignalAction


def make_order(**overrides) -> OrderRequest:
    base = dict(
        signal_id="sig-1",
        symbol="NIFTY",
        action=SignalAction.BUY,
        side=OrderSide.BUY,
        quantity=75,
        order_type=OrderType.MARKET,
        product_type=ProductType.INTRADAY,
        broker_symbol={"dhan": {"security_id": "13", "exchange_segment": "IDX_I"}},
    )
    base.update(overrides)
    return OrderRequest(**base)


def _adapter_with_transport(handler) -> DhanAdapter:
    adapter = DhanAdapter(client_id="client-1", access_token="token-1", base_url="https://api.dhan.co/v2")
    adapter._client = httpx.Client(
        base_url="https://api.dhan.co/v2",
        transport=httpx.MockTransport(handler),
    )
    return adapter


def test_place_order_success():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = httpx.Request(request.method, request.url, content=request.content).content
        return httpx.Response(200, json={"orderId": "ORD123", "orderStatus": "PENDING"})

    adapter = _adapter_with_transport(handler)
    result = adapter.place_order(make_order())

    assert result.status == OrderStatus.ACCEPTED
    assert result.broker_order_id == "ORD123"
    body = json.loads(captured["body"])
    assert body["transactionType"] == "BUY"
    assert body["securityId"] == "13"


def test_place_order_rejected_by_api():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"errorMessage": "insufficient funds"}')

    adapter = _adapter_with_transport(handler)
    result = adapter.place_order(make_order())

    assert result.status == OrderStatus.REJECTED
    assert "400" in result.message


def test_place_order_without_symbol_mapping_is_rejected_locally():
    adapter = DhanAdapter(client_id="c", access_token="t", base_url="https://api.dhan.co/v2")
    result = adapter.place_order(make_order(broker_symbol={}))

    assert result.status == OrderStatus.REJECTED
    assert "No DHAN security_id" in result.message


def test_missing_credentials_raise():
    import pytest

    with pytest.raises(ValueError):
        DhanAdapter(client_id="", access_token="", base_url="https://api.dhan.co/v2")
