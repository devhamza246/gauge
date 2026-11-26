"""Capture every LLM call made while one dataset case runs.

Three ways to record a call:

* ``@observe(model="...")``      decorator for your own LLM function (tokens estimated unless returned)
* ``wrap_anthropic(client)`` /   proxies that record real token usage automatically
  ``wrap_openai(client)``
* ``record_call(...)``           explicit, for any other provider or a local model
"""
from __future__ import annotations

import contextvars
import functools
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .models import LLMCall
from .pricing import cost_usd

_current: contextvars.ContextVar[list[LLMCall] | None] = contextvars.ContextVar("gauge_calls", default=None)


@contextmanager
def capture() -> Iterator[list[LLMCall]]:
    """Collect calls made inside the block (and inside threads that copy the context)."""
    calls: list[LLMCall] = []
    token = _current.set(calls)
    try:
        yield calls
    finally:
        _current.reset(token)


def record_call(*, model: str, prompt: Any = None, output: Any = None, input_tokens: int = 0,
                output_tokens: int = 0, latency_ms: float = 0.0, purpose: str = "app",
                error: str | None = None, cost: float | None = None) -> LLMCall:
    call = LLMCall(
        model=model, prompt=prompt, output=output, input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0), latency_ms=float(latency_ms), purpose=purpose, error=error,
        cost_usd=cost if cost is not None else cost_usd(model, int(input_tokens or 0), int(output_tokens or 0)),
    )
    calls = _current.get()
    if calls is not None:
        calls.append(call)
    return call


def estimate_tokens(value: Any) -> int:
    """Rough fallback (about 4 characters per token) used only when a provider reports no usage."""
    return max(1, len(str(value)) // 4) if value not in (None, "") else 0


def observe(model: str = "unknown", *, purpose: str = "app") -> Callable:
    """Decorator. If the function returns ``(output, {"input_tokens": n, "output_tokens": m})`` the
    usage is recorded; otherwise tokens are estimated from the text."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            error = None
            result: Any = None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as exc:  # recorded, then re-raised
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                usage: dict[str, Any] = {}
                output = result
                if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
                    output, usage = result
                prompt = args[0] if args else kwargs
                record_call(
                    model=model, prompt=prompt, output=output,
                    input_tokens=usage.get("input_tokens") or estimate_tokens(prompt),
                    output_tokens=usage.get("output_tokens") or estimate_tokens(output),
                    latency_ms=(time.perf_counter() - start) * 1000, purpose=purpose, error=error,
                )
        return inner
    return deco


class _Proxy:
    def __init__(self, target: Any, patched: dict[str, Callable]):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_patched", patched)

    def __getattr__(self, name: str) -> Any:
        patched = object.__getattribute__(self, "_patched")
        if name in patched:
            return patched[name]
        return getattr(object.__getattribute__(self, "_target"), name)


def _wrap_namespace(obj: Any, name: str) -> Any:
    patched: dict[str, Callable] = {}
    if name == "messages" and hasattr(obj, "create"):
        patched["create"] = _traced(obj.create, _anthropic_usage, _anthropic_text)
    if name == "completions" and hasattr(obj, "create"):
        patched["create"] = _traced(obj.create, _openai_usage, _openai_text)
    return _Proxy(obj, patched)


def _traced(fn: Callable, usage_of: Callable, text_of: Callable) -> Callable:
    @functools.wraps(fn)
    def inner(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        model = kwargs.get("model", "unknown")
        prompt = {k: kwargs[k] for k in ("system", "messages") if k in kwargs}
        try:
            response = fn(*args, **kwargs)
        except Exception as exc:
            record_call(model=model, prompt=prompt, latency_ms=(time.perf_counter() - start) * 1000,
                        error=f"{type(exc).__name__}: {exc}")
            raise
        tokens_in, tokens_out = usage_of(response)
        record_call(model=getattr(response, "model", None) or model, prompt=prompt, output=text_of(response),
                    input_tokens=tokens_in, output_tokens=tokens_out,
                    latency_ms=(time.perf_counter() - start) * 1000)
        return response
    return inner


def _anthropic_usage(r: Any) -> tuple[int, int]:
    u = getattr(r, "usage", None)
    return int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0)


def _anthropic_text(r: Any) -> str:
    return "".join(getattr(b, "text", "") for b in getattr(r, "content", []) or [])


def _openai_usage(r: Any) -> tuple[int, int]:
    u = getattr(r, "usage", None)
    return int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0)


def _openai_text(r: Any) -> str:
    choices = getattr(r, "choices", None) or []
    return getattr(getattr(choices[0], "message", None), "content", "") if choices else ""


def wrap_anthropic(client: Any) -> Any:
    """Return a client whose ``messages.create`` calls are recorded with real token usage."""
    return _Proxy(client, {"messages": _wrap_namespace(client.messages, "messages")})


def wrap_openai(client: Any) -> Any:
    """Return a client whose ``chat.completions.create`` calls are recorded with real token usage."""
    chat = _Proxy(client.chat, {"completions": _wrap_namespace(client.chat.completions, "completions")})
    return _Proxy(client, {"chat": chat})
