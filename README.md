# DataVeda Nifty Agent

An agent that receives strategy signals from TradingView (via webhook
alerts) and executes trades on DHAN and/or MetaTrader 5, through a common
broker-adapter interface so more brokers can be added later without
touching the core pipeline.

```
TradingView alert (webhook) --> FastAPI endpoint --> auth + risk checks --> order router --> broker adapter(s) --> DHAN / MT5
                                                            |
                                                       SQLite trade log
                                                    (idempotency + audit trail)
```

## ⚠️ Live trading is enabled by default

`TRADING_MODE=live` in `.env.example` — as soon as you configure real
broker credentials and start the agent, incoming signals place real orders
with real money. Set `TRADING_MODE=paper` while testing; paper mode
validates and logs every signal exactly like live mode but never calls a
broker. There's also a runtime kill switch (`POST /admin/kill-switch`) to
halt trading instantly without restarting the container, and a
`KILL_SWITCH=true` env var for a hard stop at startup.

## Project layout

```
app/
  config.py            Settings loaded from .env
  main.py              FastAPI app: /webhook/tradingview, /admin/*, /health
  models/              Pydantic signal schema + internal order dataclasses
  brokers/
    base.py            BrokerAdapter interface every broker implements
    dhan.py             DHAN HQ REST API v2 adapter
    mt5.py              MetaTrader 5 adapter (direct or HTTP-bridge mode)
    registry.py         Builds enabled broker instances from config
  mt5_core.py           Actual MetaTrader5 package calls (Windows-only)
  mt5_bridge/server.py  Standalone bridge server to run on a Windows host
  risk/manager.py       Kill switch, symbol allowlist, qty/loss/position caps
  services/order_router.py  Idempotency, action->side/qty resolution, fan-out
  security/auth.py      Passphrase + IP-allowlist checks
  storage/               SQLite trade log + kill-switch state
pine/example_strategy_alert.md   Pine Script + alert JSON template
tests/                  pytest suite (40 tests, all broker calls mocked)
```

## Quickstart (paper mode, local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set WEBHOOK_PASSPHRASE, set TRADING_MODE=paper for now

cp symbol_map.example.json symbol_map.json
# edit symbol_map.json: map the symbols your strategy trades to real
# broker instrument IDs (DHAN security_id, MT5 symbol name)

uvicorn app.main:app --reload
```

Send a test signal:

```bash
curl -X POST http://localhost:8000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{
    "passphrase": "your-webhook-passphrase",
    "signal_id": "test-1",
    "strategy": "manual_test",
    "symbol": "NIFTY",
    "action": "buy",
    "quantity": 75
  }'
```

Run the tests:

```bash
pip install pytest
pytest -q
```

## Configuring TradingView

See `pine/example_strategy_alert.md` for the Pine Script `alert()` pattern
and the JSON payload TradingView should POST. Key points:

- Every payload must include `"passphrase"` matching `WEBHOOK_PASSPHRASE`.
- Every payload must include a unique `"signal_id"` (e.g. built from
  `{{ticker}}-{{interval}}-{{time}}`) — this is how retried/duplicate
  webhook deliveries are safely ignored instead of double-executing.
- `"symbol"` must be a key in `symbol_map.json`.
- TradingView requires HTTPS webhook URLs, so put a reverse proxy with TLS
  (Caddy, nginx+certbot, a cloud load balancer) in front of the container
  when deployed.

## DHAN setup

1. Generate an access token from the DHAN web app: My Profile > DhanHQ
   Trading APIs.
2. Set `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in `.env`.
3. In `symbol_map.json`, map each symbol to DHAN's `security_id` and
   `exchange_segment` (e.g. `NSE_EQ`, `NSE_FNO`, `IDX_I`). Look these up via
   DHAN's instrument master CSV/API — they are not the same as the trading
   symbol string.

## MetaTrader 5 setup

The `MetaTrader5` Python package only works on Windows, because it talks to
a locally running MT5 terminal. Two deployment options:

**Direct mode** (`MT5_MODE=direct`) — run the whole agent (this FastAPI
app) on a Windows host/VM/VPS with the MT5 terminal installed and logged
in. Install `requirements-mt5.txt` in addition to `requirements.txt`.

**Bridge mode** (`MT5_MODE=bridge`, recommended for the Dockerized/Linux
deployment) — run the main agent in Docker on Linux as normal, and run
`app/mt5_bridge/server.py` separately on a Windows host with the MT5
terminal:

```powershell
pip install -r requirements-mt5-bridge.txt
set MT5_LOGIN=12345678
set MT5_PASSWORD=your-password
set MT5_SERVER=YourBroker-Server
uvicorn app.mt5_bridge.server:app --host 0.0.0.0 --port 8811
```

Then in the main agent's `.env`:

```
MT5_MODE=bridge
MT5_BRIDGE_URL=http://<windows-host-ip>:8811
```

The bridge has no authentication of its own — only expose it on a private
network or VPN between the two hosts, never on the public internet.

In `symbol_map.json`, map each symbol to the exact MT5 symbol name your
broker uses (check MT5's Market Watch — names often have suffixes like
`EURUSD.a`).

## Risk management

Configured via `.env`:

| Setting | Purpose |
|---|---|
| `RISK_ALLOWED_SYMBOLS` | Only these symbols can be traded (empty = no restriction) |
| `RISK_MAX_QTY_PER_ORDER` | Hard cap on quantity/lots per signal |
| `RISK_MAX_OPEN_POSITIONS` | Blocks new entries once a broker has this many open positions |
| `RISK_MAX_DAILY_LOSS` | Circuit breaker: blocks all trading once today's realized loss (tracked in the trade log) reaches this |
| `KILL_SWITCH` | Hard stop at startup |

Realized PnL isn't computed automatically (that needs broker-specific trade
reconciliation) — record it via the DB (`TradeLog.realized_pnl`) as you
extend the reconciliation logic, or wire up a periodic job that pulls it
from each broker's trade book.

Runtime kill switch (no restart needed):

```bash
curl -X POST "http://localhost:8000/admin/kill-switch?engaged=true&reason=manual+halt" \
  -H "X-Admin-Token: your-webhook-passphrase"

curl "http://localhost:8000/admin/status" -H "X-Admin-Token: your-webhook-passphrase"
```

(Admin endpoints reuse `WEBHOOK_PASSPHRASE` as the token since this is a
personal-use agent — rotate it if you suspect it's leaked.)

## Adding a new broker

1. Create `app/brokers/<broker>.py` implementing `BrokerAdapter`
   (`place_order`, `get_open_positions`, `get_account_summary`, and
   optionally `get_signed_position_qty` to support `close_all` signals).
2. Add one branch to `build_brokers()` in `app/brokers/registry.py`.
3. Add `<broker>` to `ENABLED_BROKERS` and map symbols to it in
   `symbol_map.json`.

Nothing else in the pipeline (risk manager, order router, webhook handler)
needs to change.

## Docker deployment

```bash
cp .env.example .env   # fill in real values
cp symbol_map.example.json symbol_map.json
docker compose up -d --build
```

This runs the Linux-only parts (webhook, risk manager, order router, DHAN
adapter, MT5 adapter in bridge mode). Put a TLS-terminating reverse proxy
in front of it for the public HTTPS endpoint TradingView requires. If using
MT5, run `app/mt5_bridge/server.py` separately on a Windows host as
described above.

## Limitations / things to extend before relying on this for real capital

- No automatic PnL reconciliation from broker trade books — the daily-loss
  circuit breaker only sees PnL you (or a job you add) record explicitly.
- No slippage/partial-fill handling beyond what each broker's API reports
  synchronously on order placement.
- The MT5 bridge has no auth — restrict it at the network level.
- `close_all` position lookup depends on each adapter's
  `get_signed_position_qty`; verify it against your broker's actual
  position payload shape before relying on it live.
