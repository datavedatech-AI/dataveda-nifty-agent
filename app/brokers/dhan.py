import logging

import httpx

from app.brokers.base import BrokerAdapter
from app.models.order import OrderRequest, OrderResult, OrderSide, OrderStatus
from app.models.signal import OrderType, ProductType

logger = logging.getLogger(__name__)

_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
}

_PRODUCT_TYPE_MAP = {
    ProductType.INTRADAY: "INTRADAY",
    ProductType.DELIVERY: "CNC",
    ProductType.MARGIN: "MARGIN",
}


class DhanAdapter(BrokerAdapter):
    """Adapter for DHAN's HQ REST API (https://api.dhan.co/v2).

    Requires DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN (generated from the DHAN
    web app under My Profile > Access DhanHQ APIs).
    """

    name = "dhan"

    def __init__(self, client_id: str, access_token: str, base_url: str, timeout: float = 10.0):
        if not client_id or not access_token:
            raise ValueError("DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN must be configured")
        self.client_id = client_id
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "access-token": access_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        mapping = order.broker_symbol.get(self.name)
        if not mapping or "security_id" not in mapping:
            return OrderResult(
                broker=self.name,
                status=OrderStatus.REJECTED,
                message=f"No DHAN security_id mapped for symbol {order.symbol}",
            )

        payload = {
            "dhanClientId": self.client_id,
            "transactionType": "BUY" if order.side == OrderSide.BUY else "SELL",
            "exchangeSegment": mapping.get("exchange_segment", "NSE_EQ"),
            "productType": _PRODUCT_TYPE_MAP[order.product_type],
            "orderType": _ORDER_TYPE_MAP[order.order_type],
            "validity": "DAY",
            "securityId": str(mapping["security_id"]),
            "quantity": int(order.quantity),
            "disclosedQuantity": 0,
            "price": order.price or 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
        }

        try:
            resp = self._client.post("/orders", json=payload)
        except httpx.HTTPError as exc:
            logger.exception("DHAN order request failed")
            return OrderResult(broker=self.name, status=OrderStatus.ERROR, message=str(exc))

        if resp.status_code >= 400:
            return OrderResult(
                broker=self.name,
                status=OrderStatus.REJECTED,
                message=f"DHAN API error {resp.status_code}: {resp.text}",
                raw_response=resp.text,
            )

        data = resp.json()
        return OrderResult(
            broker=self.name,
            status=OrderStatus.ACCEPTED,
            broker_order_id=data.get("orderId"),
            message=data.get("orderStatus", "submitted"),
            raw_response=data,
        )

    def get_open_positions(self) -> list[dict]:
        resp = self._client.get("/positions")
        resp.raise_for_status()
        return resp.json()

    def get_account_summary(self) -> dict:
        resp = self._client.get("/fundlimit")
        resp.raise_for_status()
        return resp.json()

    def get_signed_position_qty(self, mapping: dict) -> float | None:
        security_id = str(mapping.get("security_id", ""))
        if not security_id:
            return None
        for pos in self.get_open_positions():
            if str(pos.get("securityId", "")) == security_id:
                return float(pos.get("netQty", 0))
        return None

    def close(self):
        self._client.close()
