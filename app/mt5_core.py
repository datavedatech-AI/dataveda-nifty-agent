"""Actual MetaTrader5 terminal interaction.

Only importable on a host that has the `MetaTrader5` package installed,
which in turn requires a Windows OS (native or a Windows VM/VPS) with the
MT5 terminal present. This module is used by:

  - app/brokers/mt5.py's MT5Adapter, when MT5_MODE=direct (the agent
    process itself runs on the Windows host with the terminal).
  - app/mt5_bridge/server.py, a tiny standalone HTTP server you run on the
    Windows host when MT5_MODE=bridge (the main agent runs elsewhere, e.g.
    a Linux Docker container, and talks to this bridge over HTTP).

Never imported by the main FastAPI app when MT5_MODE=bridge, so the
Linux/Docker deployment never needs the MetaTrader5 package installed.
"""

import MetaTrader5 as mt5  # type: ignore[import-not-found]


class MT5Client:
    def __init__(self, login: str = "", password: str = "", server: str = "", terminal_path: str = ""):
        self.login = login
        self.password = password
        self.server = server
        self.terminal_path = terminal_path
        self._initialized = False

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        kwargs: dict = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        if self.login:
            kwargs.update(login=int(self.login), password=self.password, server=self.server)
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
        self._initialized = True

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        price: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
    ) -> dict:
        self._ensure_init()

        if not mt5.symbol_select(symbol, True):
            return {"status": "rejected", "message": f"symbol_select failed for {symbol}: {mt5.last_error()}"}

        is_buy = side == "buy"
        request: dict = {
            "symbol": symbol,
            "volume": float(quantity),
            "deviation": 20,
            "magic": 100001,
            "comment": "dataveda-nifty-agent",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        if order_type == "market":
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return {"status": "rejected", "message": f"no tick data for {symbol}"}
            request["action"] = mt5.TRADE_ACTION_DEAL
            request["type"] = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
            request["price"] = tick.ask if is_buy else tick.bid
        else:
            if price is None:
                return {"status": "rejected", "message": "price required for limit orders"}
            request["action"] = mt5.TRADE_ACTION_PENDING
            request["type"] = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT
            request["price"] = price

        if sl:
            request["sl"] = sl
        if tp:
            request["tp"] = tp

        result = mt5.order_send(request)
        if result is None:
            return {"status": "error", "message": f"order_send returned None: {mt5.last_error()}"}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {
                "status": "rejected",
                "message": f"retcode={result.retcode} comment={result.comment}",
                "raw": result._asdict(),
            }
        return {
            "status": "accepted",
            "order_id": str(result.order),
            "message": result.comment,
            "raw": result._asdict(),
        }

    def get_positions(self) -> list[dict]:
        self._ensure_init()
        positions = mt5.positions_get()
        return [p._asdict() for p in positions] if positions else []

    def get_account_summary(self) -> dict:
        self._ensure_init()
        info = mt5.account_info()
        return info._asdict() if info else {}
