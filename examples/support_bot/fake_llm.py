"""A deterministic, offline stand-in for an LLM so the example runs free in CI.

It is a SIMULATION: a keyword classifier whose behaviour follows the instructions in the system prompt,
so degrading the prompt degrades the output the way a real model would. Replace `complete` with a real
client (wrapped by gauge.wrap_anthropic / wrap_openai) to evaluate a real model.
"""
import hashlib
import json
import time

from gauge.tracing import estimate_tokens

CATEGORY_WORDS = {
    "billing": ("invoice", "charged", "refund", "payment", "billing", "card", "subscription price"),
    "technical": ("error", "crash", "bug", "not loading", "api", "timeout", "500", "broken", "slow"),
    "account": ("password", "login", "log in", "locked", "email address", "two-factor", "profile", "username"),
    "shipping": ("package", "delivery", "tracking", "shipped", "courier", "arrived", "address label"),
}
HIGH_WORDS = ("urgent", "cannot", "can't", "unable", "locked out", "charged twice", "down", "asap", "blocked")
LOW_WORDS = ("question", "wondering", "how do i", "curious", "is it possible", "just checking")


def _category(ticket: str) -> str:
    text = ticket.lower()
    scores = {c: sum(w in text for w in words) for c, words in CATEGORY_WORDS.items()}
    best = max(scores, key=lambda c: (scores[c], c))
    return best if scores[best] else "technical"


def _priority(ticket: str) -> str:
    text = ticket.lower()
    if any(w in text for w in HIGH_WORDS):
        return "high"
    if any(w in text for w in LOW_WORDS):
        return "low"
    return "medium"


def complete(system: str, ticket: str) -> tuple[str, dict]:
    jitter = int(hashlib.sha256(ticket.encode()).hexdigest(), 16) % 12  # stable 0-11 ms per input
    time.sleep((8 + jitter) / 1000)
    category, priority = _category(ticket), _priority(ticket)
    if "JSON object" not in system:
        text = f"Thanks for reaching out! This looks like a {category} matter and seems {priority} priority."
    elif "priority" not in system.lower():
        text = json.dumps({"category": category})
    else:
        text = json.dumps({"category": category, "priority": priority})
    return text, {"input_tokens": estimate_tokens(system + ticket), "output_tokens": estimate_tokens(text)}
