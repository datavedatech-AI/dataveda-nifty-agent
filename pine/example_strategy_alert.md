# TradingView alert setup

## 0. Sign up first

```
POST /signup   {"email": "you@example.com"}
```

Save the response - `webhook_url`, `webhook_passphrase`, and `agent_token` are
shown exactly once and can't be retrieved again. Then:

1. Map your symbols: `PUT /me/symbol-map` with `Authorization: Bearer <api_key>`,
   body `{"symbol_map": {"EURUSD": "EURUSD"}}` (TradingView symbol -> your
   broker's MT5 symbol name - check MT5's Market Watch, names often have
   suffixes like `EURUSD.a`).
2. Start your bridge agent on the Windows machine with your MT5 terminal:
   `python bridge_agent/agent.py --server wss://<your-domain>/agent/ws --token <agent_token>`

## 1. Minimal Pine Script strategy with alertcondition

```pinescript
//@version=5
strategy("My EMA Cross", overlay=true)

fastLen = input.int(9, "Fast EMA")
slowLen = input.int(21, "Slow EMA")

fastEma = ta.ema(close, fastLen)
slowEma = ta.ema(close, slowLen)

longCondition = ta.crossover(fastEma, slowEma)
shortCondition = ta.crossunder(fastEma, slowEma)

if longCondition
    strategy.entry("Long", strategy.long)
    alert('{"action":"buy"}', alert.freq_once_per_bar_close)

if shortCondition
    strategy.close("Long")
    alert('{"action":"close_long"}', alert.freq_once_per_bar_close)
```

## 2. Alert message template

```json
{
  "passphrase": "REPLACE_WITH_YOUR_WEBHOOK_PASSPHRASE",
  "signal_id": "{{ticker}}-{{interval}}-{{time}}",
  "strategy": "my_ema_cross",
  "symbol": "EURUSD",
  "action": "buy",
  "quantity": 0.1,
  "order_type": "market"
}
```

- `passphrase` - must exactly match the `webhook_passphrase` from your signup response.
- `signal_id` - MUST be unique per alert firing. `{{time}}` (bar close time)
  combined with `{{ticker}}` and `{{interval}}` is a reliable choice. This is
  what lets the agent safely ignore TradingView's automatic retry of a
  webhook delivery instead of double-placing the order.
- `action` - one of `buy`, `sell`, `close_long`, `close_short`. (`close_all`
  isn't supported yet - use `close_long`/`close_short` with an explicit quantity.)
- `symbol` - must be a key in your account's symbol map (`PUT /me/symbol-map`).

For a limit order, add `"order_type": "limit"` and `"price": {{close}}` (or
another TradingView placeholder).

## 3. Webhook URL

Point the alert's webhook URL at:

```
https://<your-domain>/webhook/tradingview/<your-webhook-id>
```

(the full path, including the ID, is in your signup response as `webhook_url`).

TradingView requires HTTPS for webhooks, so the server needs a reverse
proxy with TLS (Caddy, nginx + certbot, or a managed load balancer) in
front of it.
