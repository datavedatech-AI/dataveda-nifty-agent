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
web/                      Dashboard (static HTML/CSS/vanilla JS, no build step) - served
                          by the same FastAPI app at "/"
bridge_agent/agent.py     Self-contained script customers run next to their MT5 terminal
pine/example_strategy_alert.md   Pine Script + alert JSON template
tests/                    pytest suite (async, WS round-trip tested end-to-end)
```

## Dashboard

Served at `/` by the same FastAPI app (`web/`, mounted last so it never
shadows an API route). Sign-up and login both work from the browser - login
is just "paste your API key," stored in the browser's localStorage and sent
as a Bearer token on every request; there's no separate password/session
system yet (see "What's deliberately not built yet"). From the dashboard
you can:

- Sign up / log in
- See bridge agent connection status, kill-switch state, today's P&L
- Engage/resume the kill switch
- View (and copy) your webhook URL, and regenerate the webhook passphrase
  or agent token if either leaks
- Edit the symbol map and risk settings
- Browse recent trades (auto-refreshes every 5s while the tab is open)

It talks to the same JSON API documented below - nothing in `/me/*` is
dashboard-only.

## Subscriptions (manual billing)

Payment is handled outside the app - a customer pays you directly (bank
transfer, UPI, whatever), and you grant them access. There's no
self-service checkout.

A brand new signup **cannot trade**: `Tenant.subscription_expires_at` is
`None` until you grant it, and the subscription check is the very first
thing `check_risk` evaluates (before the kill switch, before anything
else) - every signal gets rejected with "No active subscription" until
you do. Once you receive payment:

```bash
curl -X POST http://localhost:8000/admin/tenants/<tenant_id>/subscription \
  -H "X-Admin-Key: <ADMIN_API_KEY>" -H "Content-Type: application/json" \
  -d '{"days": 30}'
```

or use the admin page at `/admin` (paste `ADMIN_API_KEY` to log in) -
find the tenant, click Grant. Renewing early stacks onto the existing
expiry instead of resetting it, so paying a few days before expiry never
costs the customer those remaining days; renewing a lapsed account starts
fresh from now. `POST /admin/tenants/{id}/revoke-subscription` cuts
access immediately (chargebacks, disputes).

**`ADMIN_API_KEY` must be set** (`.env` or the environment) before any
`/admin/*` route works - unset, they fail closed (401) rather than
silently allowing access. This key is yours, not a customer's; keep it
out of anything customer-facing.

## Admin page

`/admin` - separate from the customer dashboard, logged in with
`ADMIN_API_KEY` (stored in the browser's localStorage under a different
key than the customer login, so the two never collide in the same
browser). Lists every tenant with their subscription/trading status and
lets you grant or revoke access without curl. An "Expiring soon only"
checkbox filters the list to accounts you should follow up with for renewal.

## Expiring-soon reminder

There's no email/SMS here - no provider is wired up, and picking one
(SendGrid, SES, whatever) wasn't part of this build. What exists instead
is in-app: `subscription_status()` (`app/storage/repository.py`) computes
`subscription_expiring_soon` and `subscription_days_remaining` whenever a
tenant is within `SUBSCRIPTION_EXPIRING_SOON_DAYS` (7, by default) of
their expiry, and both `/me` and `/admin/tenants` return it.

- **Customer dashboard**: an amber banner ("expires in N days") replaces
  the usual hidden-when-healthy state, distinct from the red banner shown
  once trading is actually blocked.
- **Admin page**: an amber pill per tenant, plus the "Expiring soon only"
  filter above, so you know who to chase for a renewal before they lapse.

If you want actual email/SMS reminders later, `subscription_status()` is
the one place that would need a scheduled job built on top of it (check
tenants where `subscription_expiring_soon` is true, on some cadence, send
once per tenant per expiry so it doesn't re-notify on every check).

## Quickstart (local dev)

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# for local dev, DATABASE_URL=sqlite+aiosqlite:///./data/tenants.db is fine -
# Postgres (requirements-postgres.txt) is only needed for a real deployment

uvicorn app.main:app --reload
```

Windows (PowerShell) - note `&&` isn't valid PowerShell syntax, run each line separately:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

copy .env.example .env
# edit .env: DATABASE_URL=sqlite+aiosqlite:///./data/tenants.db

uvicorn app.main:app --reload
```

`requirements-dev.txt` never installs `asyncpg` (the Postgres driver) -
it has no prebuilt wheel for every Python/OS combo (Python 3.13 on
Windows notably) and pip falls back to compiling it from source, which
needs a matching MSVC toolchain and can fail outright against newer
CPython internals. Local dev and tests only need SQLite
(`aiosqlite`, already included). Only pull in `requirements-postgres.txt`
when you're actually pointing `DATABASE_URL` at a real Postgres server -
the Docker image does this for you already.

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
| `GET /me/trades` | Bearer api_key | Recent trade log, newest first (`?limit=&offset=`) |
| `POST /me/regenerate-webhook-passphrase` | Bearer api_key | Rotate the passphrase (old one stops working immediately) |
| `POST /me/regenerate-agent-token` | Bearer api_key | Rotate the agent token (any connected agent must reconnect with the new one) |
| `POST /webhook/tradingview/{webhook_id}` | passphrase in body | TradingView alerts land here |
| `WS /agent/ws?token=<agent_token>` | agent_token | The bridge agent's persistent connection |
| `GET /admin/tenants` | `X-Admin-Key` | List all tenants + subscription/trading status |
| `POST /admin/tenants/{id}/subscription` | `X-Admin-Key` | Grant/extend access (`{"days": 30}`) |
| `POST /admin/tenants/{id}/revoke-subscription` | `X-Admin-Key` | Immediate cutoff |
| `GET /health` | none | Liveness check |
| `GET /` | none | Customer dashboard (static files from `web/`) |
| `GET /admin` | none (page itself; API calls it makes need the key) | Admin page |

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

- **Real login.** The dashboard authenticates by pasting the API key shown
  at signup (stored in the browser's localStorage) - there's no password,
  email verification, or session system, and a lost key has no recovery
  path other than signing up again. Fine for early users, not for a
  public launch.
- **Self-service billing.** Subscriptions are enforced (see above) but
  granted manually by you, not purchased - no Stripe/payment integration,
  no invoicing, no automatic renewal reminder before a subscription lapses.
  `plan` exists on the Tenant model as a placeholder for tiered pricing later.
- **Multi-broker.** MT5-only by design (see the SEBI/regulatory discussion
  this pivot came out of) - a DHAN adapter existed in an earlier version
  and can be reintroduced as an async adapter later.
- **Horizontal scaling of the WS relay** (see Latency notes above).
