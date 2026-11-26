"""App under test: a support-ticket triage bot. Returns {"category", "priority"} (or raw text if the model drifts)."""
import json
import time

from gauge import record_call

from .fake_llm import complete
from .prompts import TRIAGE_SYSTEM

MODEL = "simulated-llm"


def _llm(ticket: str) -> str:
    start = time.perf_counter()
    text, usage = complete(TRIAGE_SYSTEM, ticket)
    # Record the *full* prompt so a failing case shows exactly what the model was given.
    record_call(model=MODEL, prompt=f"[system]\n{TRIAGE_SYSTEM}\n\n[user]\n{ticket}", output=text,
                input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                latency_ms=(time.perf_counter() - start) * 1000)
    return text


def triage(ticket: str):
    text = _llm(ticket)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
