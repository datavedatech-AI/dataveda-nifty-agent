import logging

import httpx

from app.brokers.base import BrokerAdapter
from app.models.order import OrderRequest, OrderResult, OrderSide, OrderStatus

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    "accepted": OrderStatus.ACCEPTED,
    "rejected": OrderStatus.REJECTED,
    "error": OrderStatus.ERROR,
}


class MT5Adapter(BrokerAdapter):
    """Adapter for MetaTrader 5.

    Two modes (set via MT5_MODE):
      - "direct": this process itself runs on the Windows host with the MT5
        terminal installed; talks to it in-process via app.mt5_core.
      - "bridge": the MT5 terminal runs on a separate Windows host running
        app/mt5_bridge/server.py; this adapter talks to it over HTTP. Use
        this when the main agent runs in a Linux Docker container, since
        the MetaTrader5 python package requires Windows.
    """

    name = "mt5"

    def __init__(
        self,
        mode: str = "direct",
        login: str = "",
        password: str = "",
        server: str = "",
        terminal_path: str = "",
        bridge_url: str = "",
    ):
        self.mode = mode
        self._client = None
        self._http = None

        if mode == "direct":
            from app.mt5_core import MT5Client  # imported lazily: requires Windows + MetaTrader5 pkg

            self._client = MT5Client(login, password, server, terminal_path)
        elif mode == "bridge":
            if not bridge_url:
                raise ValueError("MT5_BRIDGE_URL must be set when MT5_MODE=bridge")
            self._http = httpx.Client(base_url=bridge_url, timeout=10.0)
        else:
            raise ValueError(f"Unknown MT5_MODE: {mode!r}, expected 'direct' or 'bridge'")

    def place_order(self, order: OrderRequest) -> OrderResult:
        mapping = order.broker_symbol.get(self.name)
        if not mapping or "symbol" not in mapping:
            return OrderResult(
                broker=self.name,
                status=OrderStatus.REJECTED,
                message=f"No MT5 symbol mapped for {order.symbol}",
            )
        symbol = mapping["symbol"]
        side = "buy" if order.side == OrderSide.BUY else "sell"

        try:
            if self.mode == "direct":
                result = self._client.place_order(
                    symbol, side, order.quantity, order.order_type.value, order.price, order.stop_loss, order.take_profit
                )
            else:
                resp = self._http.post(
                    "/order",
                    json={
                        "symbol": symbol,
                        "side": side,
                        "quantity": order.quantity,
                        "order_type": order.order_type.value,
                        "price": order.price,
                        "stop_loss": order.stop_loss,
                        "take_profit": order.take_profit,
                    },
                )
                resp.raise_for_status()
                result = resp.json()
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.exception("MT5 order request failed")
            return OrderResult(broker=self.name, status=OrderStatus.ERROR, message=str(exc))

        return OrderResult(
            broker=self.name,
            status=_STATUS_MAP.get(result.get("status"), OrderStatus.ERROR),
            broker_order_id=result.get("order_id"),
            message=result.get("message", ""),
            raw_response=result.get("raw"),
        )

    def get_open_positions(self) -> list[dict]:
        if self.mode == "direct":
            return self._client.get_positions()
        resp = self._http.get("/positions")
        resp.raise_for_status()
        return resp.json()

    def get_account_summary(self) -> dict:
        if self.mode == "direct":
            return self._client.get_account_summary()
        resp = self._http.get("/account")
        resp.raise_for_status()
        return resp.json()

    def get_signed_position_qty(self, mapping: dict) -> float | None:
        symbol = mapping.get("symbol", "")
        if not symbol:
            return None
        for pos in self.get_open_positions():
            if pos.get("symbol") == symbol:
                volume = float(pos.get("volume", 0))
                return volume if pos.get("type") == 0 else -volume
        return None
