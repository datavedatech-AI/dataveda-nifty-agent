"""Standalone MT5 bridge server.

Run this on the Windows host/VM/VPS that has the MetaTrader5 terminal
installed and logged in. The main agent (typically running in a Linux
Docker container) talks to this over HTTP when MT5_MODE=bridge.

Setup on the Windows host:
    pip install -r requirements-mt5-bridge.txt
    set MT5_LOGIN=12345678
    set MT5_PASSWORD=your-password
    set MT5_SERVER=YourBroker-Server
    uvicorn app.mt5_bridge.server:app --host 0.0.0.0 --port 8811

Then on the main agent's .env:
    MT5_MODE=bridge
    MT5_BRIDGE_URL=http://<windows-host-ip>:8811

This bridge has no authentication of its own - it is meant to run on a
private network/VPN between the two hosts. Do not expose it to the public
internet.
"""

import os

from fastapi import FastAPI
from pydantic import BaseModel

from app.mt5_core import MT5Client

app = FastAPI(title="DataVeda MT5 Bridge")

_client = MT5Client(
    login=os.environ.get("MT5_LOGIN", ""),
    password=os.environ.get("MT5_PASSWORD", ""),
    server=os.environ.get("MT5_SERVER", ""),
    terminal_path=os.environ.get("MT5_TERMINAL_PATH", ""),
)


class OrderIn(BaseModel):
    symbol: str
    side: str
    quantity: float
    order_type: str = "market"
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


@app.post("/order")
def order(payload: OrderIn) -> dict:
    return _client.place_order(
        payload.symbol, payload.side, payload.quantity, payload.order_type, payload.price, payload.stop_loss, payload.take_profit
    )


@app.get("/positions")
def positions() -> list[dict]:
    return _client.get_positions()


@app.get("/account")
def account() -> dict:
    return _client.get_account_summary()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
