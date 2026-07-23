# 5. Notional Cost from a single dated Rate Card, not real spend

Date: 2026-07-22

The `cost` view reports **Notional Cost** — token usage priced at published API rates — as a *comparison weight*, explicitly not money paid. On a subscription, per-session dollars are notional; the only real money is account-level **Real Spend**, which Anthropic does not attribute to any session. We investigated `xu` in `plan-usage-history.json` as a local source for Real Spend and rejected it: it does not match the authoritative claude.ai figure (two machines read ~$80 while the account meter showed ~$202). With no trustworthy local source, Standup reports **no Real Spend at all** — no number, no pointer, no estimate. It reports only what it can derive truthfully from the logs.

We price from a **single hardcoded, dated Rate Card** (`rates.py`), storing base input + output per model and *deriving* cache rates by the published multipliers (read 0.1×, 5m-write 1.25×, 1h-write 2×). Per-turn modifiers (`speed:"fast"`, `service_tier:"batch"` ×0.5, `inference_geo:"us"` ×1.1, web-search +$0.01/req) are read from each turn's own `usage`. Every model in current use (Opus 4.8, Fable 5, and — since ADR 0006 — Haiku 4.5, the brief-generation model) has an exact public rate, so nothing is estimated; a future/unknown model is shown with its tokens but excluded from the dollar total and flagged **unpriced** — never silently counted as $0 (the cost-view analogue of "never hide dirt").

## Considered alternatives

- **Live pricing API** — rejected: adds a network dependency and non-determinism to a stateless local CLI; rates move rarely.
- **Historical per-turn rates** (the rate in effect on each turn's date) — rejected: Notional Cost is a comparison weight, not a bill, so one current snapshot keeps rankings honest and the code simple. Revisit only if a model's price changes mid-window enough to distort comparisons.
- **Allocating Real Spend across sessions** proportionally — rejected: fabricates an authoritative-looking number from an unattributable total.

## Consequences

The dollar figures track the published rate card, not your invoice; they will not reconcile to Real Spend and are not meant to. The Rate Card is a maintained constant — when Anthropic changes prices or you adopt a new model, update `rates.py` and its date.
