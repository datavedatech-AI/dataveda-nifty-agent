# DataVeda MT5 Bridge

A multi-tenant service that relays TradingView strategy alerts to each
customer's own MetaTrader 5 account, over a low-latency WebSocket bridge -
no inbound ports or public IP required on the customer's machine.

```
TradingView alert (webhook)
        |
        v
 POST /webhook/tradingview/{webhook_id}  --auth: passphrase-->  tenant lookup
        |
        v
   idempotency + risk checks (DB, tenant-scoped)
        |
        v
   WebSocket relay  ---- live outbound connection ---->  customer's bridge_agent.py
        |                                                (runs next to their MT5 terminal)
        v                                                       |
   awaits agent's reply (few ms - seconds)  <---- order placed, result sent back
        |
        v
   HTTP response + trade log entry
```

## Why WebSocket, not a webhook the server calls on the customer's machine

An earlier single-tenant version of this had the server call an HTTP URL
the customer exposed. That meant per-order TCP+TLS handshakes, and every
customer needing a public IP / open port / dynamic-DNS setup - a nonstarter
for a hosted product with non-technical customers. Here, the customer's
bridge agent makes one **outbound** WebSocket connection and holds it open;
orders are pushed down that live socket the instant a signal arrives.

## Project layout

```
app/
  config.py             Server-wide settings (DATABASE_URL, log level, bridge timeout)
  main.py                FastAPI app: /signup, /me/*, /webhook/tradingview/{id}, /agent/ws, /health
  models/                 Pydantic signal schema + internal order dataclasses
  brokers/base.py         Async BrokerAdapter interface
  ws/
    manager.py             ConnectionManager: tracks each tenant's live agent
                            connection, correlates order requests with replies
    relay_broker.py         BrokerAdapter implementation over the WS relay
  risk/manager.py         Kill switch, symbol allowlist, qty/daily-loss caps (tenant-scoped)
  services/order_router.py Idempotency, action->side/qty resolution, dispatch
  storage/                 Tenant + TradeLog models, async SQLAlchemy repository
bridge_agent/agent.py     Self-contained script customers run next to their MT5 terminal
pine/example_strategy_alert.md   Pine Script + alert JSON template
tests/                    pytest suite (async, WS round-trip tested end-to-end)
```

## Quickstart (local dev)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# for local dev, DATABASE_URL=sqlite+aiosqlite:///./data/tenants.db is fine -
# Postgres is recommended once you're past solo testing

uvicorn app.main:app --reload
```

Sign up and configure an account:

```bash
curl -X POST http://localhost:8000/signup -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
# save api_key, webhook_url, webhook_passphrase, agent_token from the response - shown once

curl -X PUT http://localhost:8000/me/symbol-map \
  -H "Authorization: Bearer <api_key>" -H "Content-Type: application/json" \
  -d '{"symbol_map": {"EURUSD": "EURUSD"}}'
```

Start the bridge agent (on the machine with your MT5 terminal):

```bash
pip install -r requirements-bridge-agent.txt
python bridge_agent/agent.py --server ws://localhost:8000/agent/ws --token <agent_token>
```

Fire a test signal (see `pine/example_strategy_alert.md` for the full
TradingView alert setup):

```bash
curl -X POST http://localhost:8000<webhook_url> -H "Content-Type: application/json" \
  -d '{"passphrase": "<webhook_passphrase>", "signal_id": "test-1", "strategy": "manual_test", "symbol": "EURUSD", "action": "buy", "quantity": 0.1}'
```

Run the tests:

```bash
pytest -q
```

## API summary

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /signup` | none | Create an account, get `api_key`/`webhook_url`/`webhook_passphrase`/`agent_token` (shown once) |
| `GET /me` | Bearer api_key | Account status, symbol map, risk settings, bridge agent connection state |
| `PUT /me/symbol-map` | Bearer api_key | Set TradingView-symbol -> MT5-symbol mapping |
| `PUT /me/risk-settings` | Bearer api_key | Set `max_qty_per_order`, `max_daily_loss`, `allowed_symbols` |
| `POST /me/kill-switch` | Bearer api_key | Halt this account's trading instantly (`?engaged=true&reason=...`) |
| `POST /webhook/tradingview/{webhook_id}` | passphrase in body | TradingView alerts land here |
| `WS /agent/ws?token=<agent_token>` | agent_token | The bridge agent's persistent connection |
| `GET /health` | none | Liveness check |

## Risk management

Per-tenant, set via `PUT /me/risk-settings`:

| Setting | Purpose |
|---|---|
| `allowed_symbols` | Only these symbols can be traded (empty = no restriction) |
| `max_qty_per_order` | Hard cap on quantity/lots per signal |
| `max_daily_loss` | Circuit breaker: blocks trading once today's realized loss (from `TradeLog.realized_pnl`) reaches this |

Realized PnL isn't computed automatically yet - it needs broker-side trade
reconciliation, which is a natural next addition.

The kill switch (`POST /me/kill-switch`) takes effect immediately, no
restart needed - useful for halting a runaway strategy mid-session.

## Docker deployment

```bash
cp .env.example .env   # set POSTGRES_PASSWORD at minimum
docker compose up -d --build
```

This runs the server + Postgres. Put a TLS-terminating reverse proxy in
front of it for the public HTTPS endpoint TradingView requires (webhooks)
and the `wss://` endpoint bridge agents connect to. The bridge agent itself
is never part of this deployment - customers run it themselves via
`requirements-bridge-agent.txt`.

## Latency notes

- The WebSocket relay avoids per-order handshakes: one persistent
  connection per tenant, an order is a single JSON frame down it.
- The hot path per signal is: one idempotency-reservation write, 1-2
  indexed risk-check reads, one WS round trip, one result write - no
  synchronous calls to anything except the tenant's own bridge agent.
- For lowest latency, advise customers to run `bridge_agent.py` on a VPS
  close to their broker's trade server (many forex brokers publish
  recommended low-latency VPS locations) rather than a home PC.
- `get_open_positions()` is not fetched live over the relay (see
  `WSRelayBroker`) - each such call would add a round trip to every
  signal's hot path. `close_all` is unsupported for the same reason; use
  `close_long`/`close_short` with an explicit quantity instead.
- `ConnectionManager` is in-memory and single-process. Scaling to multiple
  server instances needs a shared layer (e.g. Redis pub/sub) so an order
  for a tenant connected to instance A can be routed from instance B -
  noted as a known next step, not yet built.

## What's deliberately not built yet

- **Web dashboard.** Everything above is JSON API + curl. A UI (signup,
  symbol map editor, live trade log, kill switch) is a natural next step
  once the core pipeline is proven.
- **Billing.** No Stripe integration; `plan` exists on the Tenant model as
  a placeholder.
- **Multi-broker.** MT5-only by design (see the SEBI/regulatory discussion
  this pivot came out of) - a DHAN adapter existed in an earlier version
  and can be reintroduced as an async adapter later.
- **Horizontal scaling of the WS relay** (see Latency notes above).
