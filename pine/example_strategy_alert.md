# TradingView alert setup

The agent expects a JSON body on every alert. TradingView lets you build
that JSON in the alert's "Message" box using placeholders that get filled
in when the alert fires.

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

`alert()` calls fire independently of the "Create Alert" dialog and let you
vary the JSON per condition, which is the recommended approach.

## 2. Alert message template

When creating the alert (or as the fixed message for a `strategy.entry`
based alert), use this JSON, filling in TradingView's built-in placeholders:

```json
{
  "passphrase": "REPLACE_WITH_YOUR_WEBHOOK_PASSPHRASE",
  "signal_id": "{{ticker}}-{{interval}}-{{time}}",
  "strategy": "my_ema_cross",
  "symbol": "NIFTY",
  "action": "buy",
  "quantity": 75,
  "order_type": "market",
  "product_type": "intraday",
  "broker": "any"
}
```

- `passphrase` - must exactly match `WEBHOOK_PASSPHRASE` in your `.env`.
- `signal_id` - MUST be unique per alert firing. `{{time}}` (bar close time)
  combined with `{{ticker}}` and `{{interval}}` is a reliable choice. This is
  what lets the agent safely ignore TradingView's automatic retry of a
  webhook delivery instead of double-placing the order.
- `action` - one of `buy`, `sell`, `close_long`, `close_short`, `close_all`.
- `symbol` - must exist as a key in `symbol_map.json` for whichever broker(s)
  you want this signal routed to.
- `broker` - `"any"` (all enabled brokers with a mapping for this symbol),
  or a specific broker name (`"dhan"` / `"mt5"`) to restrict routing.

For a limit order, add `"order_type": "limit"` and `"price": {{close}}` (or
another TradingView placeholder).

## 3. Webhook URL

Point the alert's webhook URL at:

```
https://<your-domain-or-ip>/webhook/tradingview
```

TradingView requires HTTPS for webhooks (except on paid plans testing
against `localhost` via their desktop app), so put the agent behind a
reverse proxy with TLS (Caddy, nginx + certbot, or a managed load balancer)
when deploying to a VPS.
