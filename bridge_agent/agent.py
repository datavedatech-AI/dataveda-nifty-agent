"""DataVeda MT5 Bridge Agent.

Run this on the Windows machine where your MT5 terminal is installed and
logged in. It opens an OUTBOUND WebSocket connection to the DataVeda cloud
service and holds it open - no inbound ports, no port forwarding, no
static IP needed on this machine. Orders arrive as JSON messages over that
live connection and are placed via the MetaTrader5 package immediately.

Setup:
    pip install -r requirements-bridge-agent.txt
    python bridge_agent/agent.py --server wss://your-server-host/agent/ws --token YOUR_AGENT_TOKEN

Get YOUR_AGENT_TOKEN from the response to POST /signup (shown once).

This file is intentionally self-contained (no import from the `app`
package) so it can be copied and run on its own without checking out the
whole server repository. Its MT5Client logic mirrors app/brokers - if you
change order-placement behavior there, update it here too.
"""

import argparse
import asyncio
import json
import logging

import MetaTrader5 as mt5
import websockets

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bridge-agent")


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
            "comment": "dataveda-bridge-agent",
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
            return {"status": "rejected", "message": f"retcode={result.retcode} comment={result.comment}"}
        return {"status": "accepted", "order_id": str(result.order), "message": result.comment}


async def run(server_url: str, token: str, mt5_login: str, mt5_password: str, mt5_server: str, mt5_terminal_path: str) -> None:
    client = MT5Client(mt5_login, mt5_password, mt5_server, mt5_terminal_path)
    separator = "&" if "?" in server_url else "?"
    url = f"{server_url}{separator}token={token}"

    backoff = 2
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                logger.info("Connected to %s", server_url)
                backoff = 2
                async for raw in ws:
                    message = json.loads(raw)
                    if message.get("type") != "place_order":
                        continue
                    logger.info("Order request: %s %s %s", message.get("side"), message.get("quantity"), message.get("symbol"))
                    result = client.place_order(
                        message["symbol"],
                        message["side"],
                        message["quantity"],
                        message.get("order_type", "market"),
                        message.get("price"),
                        message.get("stop_loss"),
                        message.get("take_profit"),
                    )
                    await ws.send(json.dumps({"type": "order_result", "request_id": message["request_id"], **result}))
        except (websockets.exceptions.ConnectionClosed, OSError) as exc:
            logger.warning("Disconnected (%s), reconnecting in %ss...", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def main() -> None:
    parser = argparse.ArgumentParser(description="DataVeda MT5 bridge agent")
    parser.add_argument("--server", required=True, help="wss://.../agent/ws URL of the DataVeda service")
    parser.add_argument("--token", required=True, help="Your account's agent_token from /signup")
    parser.add_argument("--mt5-login", default="", help="Leave blank to use the terminal's already-logged-in session")
    parser.add_argument("--mt5-password", default="")
    parser.add_argument("--mt5-server", default="")
    parser.add_argument("--mt5-terminal-path", default="", help="Path to terminal64.exe, if not the default install")
    args = parser.parse_args()

    asyncio.run(run(args.server, args.token, args.mt5_login, args.mt5_password, args.mt5_server, args.mt5_terminal_path))


if __name__ == "__main__":
    main()
