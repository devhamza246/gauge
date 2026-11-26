"""Token pricing in USD per million tokens. Override or extend per suite via ``pricing:`` in gauge.yml."""
from __future__ import annotations

# Prices as published by the provider when this table was written; always overridable.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
}

_overrides: dict[str, dict[str, float]] = {}


def set_pricing(overrides: dict[str, dict[str, float]] | None) -> None:
    _overrides.clear()
    _overrides.update(overrides or {})


def price_for(model: str) -> dict[str, float] | None:
    for table in (_overrides, DEFAULT_PRICING):
        if model in table:
            return table[model]
        # Allow dated/suffixed ids such as "claude-haiku-5-5-20261101".
        for key, value in table.items():
            if model.startswith(key):
                return value
    return None


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = price_for(model)
    if not price:
        return 0.0
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
