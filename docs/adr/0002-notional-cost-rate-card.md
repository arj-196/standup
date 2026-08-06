# 0002 — Notional Cost from a single dated Rate Card, not real spend

Date: 2026-07-22

The `cost` view reports **Notional Cost** — tokens priced at published API rates
— as a *comparison weight*, explicitly not money paid. On a subscription,
per-session dollars are notional; the only real money is account-level **Real
Spend**, which Anthropic does not attribute to any session.

**Standup reports no Real Spend at all** — no number, no pointer, no estimate.
`xu` in `plan-usage-history.json` was investigated as a local source and
rejected: it does not match the authoritative claude.ai figure (two machines
read ~$80 against an account meter of ~$202). With no trustworthy local source,
Standup reports only what it can derive truthfully from the logs.

Pricing comes from a **single hardcoded, dated Rate Card** (`rates.py`): base
input and output per model, cache rates *derived* by the published multipliers,
per-turn modifiers read from each turn's own `usage` rather than assumed. Every
model in current use has an exact public rate, so nothing is estimated.

**An unknown or future model is shown with its tokens but excluded from the
dollar total and flagged `unpriced`** — never silently counted as $0. That is
the cost view's analogue of "never hide dirt".

The figures track the rate card, not your invoice; they will not reconcile to
Real Spend and are not meant to. The Rate Card is a maintained constant — on a
price change or a new model, update `rates.py` and its date.

## Alternatives considered

- **Live pricing API** — adds a network dependency and non-determinism to a
  stateless local CLI; rates move rarely.
- **Historical per-turn rates** — Notional Cost is a comparison weight, not a
  bill, so one current snapshot keeps rankings honest and the code simple.
  Revisit only if a mid-window price change distorts comparisons.
- **Allocating Real Spend proportionally** — fabricates an authoritative-looking
  number from an unattributable total.
