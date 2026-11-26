"""App under test: a support-ticket triage bot. Returns {"category", "priority"} (or raw text if the model drifts)."""
import json

from gauge import observe

from .fake_llm import complete
from .prompts import TRIAGE_SYSTEM


@observe(model="simulated-llm")
def _llm(ticket: str):
    return complete(TRIAGE_SYSTEM, ticket)


def triage(ticket: str):
    text, _usage = _llm(ticket)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
