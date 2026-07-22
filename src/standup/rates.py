"""The Rate Card: model -> price, and a per-turn Notional Cost function.

Notional Cost is an API-equivalent *weight* (see CONTEXT.md / ADR 0005) — what a
turn's tokens would cost at published pay-as-you-go rates. It is not money paid.

Base input + output are stored per model; cache rates are *derived* by the
published multipliers, and per-turn modifiers (fast mode, batch tier, US
inference geo, web search) are read from the turn's own `usage` object.

Source: platform.claude.com/docs/en/about-claude/pricing (captured 2026-07-22).
Update the numbers and the date when Anthropic changes prices.
"""

from __future__ import annotations

# per-million-token base rates; "fast" is the Opus fast-mode (input, output) pair
CARD: dict[str, dict] = {
    "claude-opus-4-8":  {"in": 5.0,  "out": 25.0, "fast": (10.0, 50.0)},
    "claude-fable-5":   {"in": 10.0, "out": 50.0},
    "claude-mythos-5":  {"in": 10.0, "out": 50.0},
    "claude-sonnet-5":  {"in": 2.0,  "out": 10.0},   # introductory rate (through Aug 31 2026)
    "claude-haiku-4-5": {"in": 1.0,  "out": 5.0},
}

# cache multipliers relative to base input
CACHE_READ = 0.1
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0

WEB_SEARCH_PER_REQ = 0.01  # $10 / 1000 searches


def is_priced(model: str | None) -> bool:
    return model in CARD


def turn_cost(model: str | None, u: dict) -> float | None:
    """Notional Cost of one assistant turn, or None if the model is unpriced."""
    card = CARD.get(model or "")
    if card is None:
        return None
    base_in, out_rate = card["in"], card["out"]
    if (u.get("speed") or "standard") == "fast" and "fast" in card:
        base_in, out_rate = card["fast"]

    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens", 0)
    w1 = cc.get("ephemeral_1h_input_tokens", 0)
    if not (w5 or w1):  # older logs: undifferentiated cache-write, assume 5m
        w5 = u.get("cache_creation_input_tokens", 0)

    dollars = (
        u.get("input_tokens", 0) * base_in
        + u.get("output_tokens", 0) * out_rate
        + u.get("cache_read_input_tokens", 0) * base_in * CACHE_READ
        + w5 * base_in * CACHE_WRITE_5M
        + w1 * base_in * CACHE_WRITE_1H
    ) / 1_000_000

    if u.get("service_tier") == "batch":
        dollars *= 0.5
    if u.get("inference_geo") == "us":
        dollars *= 1.1
    stu = u.get("server_tool_use") or {}
    dollars += stu.get("web_search_requests", 0) * WEB_SEARCH_PER_REQ
    return dollars


def turn_tokens(u: dict) -> dict[str, int]:
    """Four display buckets for one turn."""
    cw = u.get("cache_creation_input_tokens", 0)
    if not cw:
        cc = u.get("cache_creation") or {}
        cw = cc.get("ephemeral_5m_input_tokens", 0) + cc.get("ephemeral_1h_input_tokens", 0)
    return {
        "input": u.get("input_tokens", 0),
        "output": u.get("output_tokens", 0),
        "cache_write": cw,
        "cache_read": u.get("cache_read_input_tokens", 0),
    }
