"""LLM judge access with record/replay cassettes so CI is free, offline and deterministic.

Modes: ``replay`` (cassette only; a miss is an error), ``live`` (always call the provider and record),
``auto`` (default: use the cassette on a hit, call the provider on a miss and record it).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .tracing import record_call


class JudgeError(RuntimeError):
    pass


@dataclass
class JudgeReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


Provider = Callable[[str, str, str], JudgeReply]  # (model, system, user) -> reply


def anthropic_provider(api_key: str | None = None, base_url: str = "https://api.anthropic.com") -> Provider:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    def call(model: str, system: str, user: str) -> JudgeReply:
        if not key:
            raise JudgeError("ANTHROPIC_API_KEY is not set (judge mode needs it for uncached calls)")
        payload = {"model": model, "max_tokens": 700, "system": system, "temperature": 0,
                   "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        response = httpx.post(f"{base_url}/v1/messages", headers=headers, json=payload, timeout=60)
        if response.status_code == 400 and "temperature" in response.text.lower():
            payload.pop("temperature")  # some models fix their own sampling; retry without it
            response = httpx.post(f"{base_url}/v1/messages", headers=headers, json=payload, timeout=60)
        if response.status_code == 401:
            raise JudgeError("the Anthropic API rejected the key (401): check ANTHROPIC_API_KEY")
        if response.status_code >= 400:
            raise JudgeError(f"judge provider returned {response.status_code}: {response.text[:200]}")
        body = response.json()
        text = "".join(block.get("text", "") for block in body.get("content", []))
        usage = body.get("usage", {})
        return JudgeReply(text, usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    return call


def openai_provider(api_key: str | None = None, base_url: str = "https://api.openai.com/v1") -> Provider:
    key = api_key or os.environ.get("OPENAI_API_KEY")

    def call(model: str, system: str, user: str) -> JudgeReply:
        if not key:
            raise JudgeError("OPENAI_API_KEY is not set (judge mode needs it for uncached calls)")
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"authorization": f"Bearer {key}"},
            json={"model": model, "temperature": 0,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=60,
        )
        if response.status_code >= 400:
            raise JudgeError(f"judge provider returned {response.status_code}: {response.text[:200]}")
        body = response.json()
        usage = body.get("usage", {})
        return JudgeReply(body["choices"][0]["message"]["content"],
                          usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    return call


class Judge:
    def __init__(self, model: str = "claude-haiku-5-5", provider: Provider | None = None,
                 mode: str = "auto", cassette: str | Path | None = None):
        if mode not in ("auto", "live", "replay"):
            raise JudgeError(f"unknown judge mode: {mode}")
        self.model = model
        self.mode = mode
        self.provider = provider
        self.cassette = Path(cassette) if cassette else None
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        if self.cassette and self.cassette.is_file():
            for line in self.cassette.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._cache[entry["key"]] = entry

    @classmethod
    def from_config(cls, config: dict[str, Any], base_dir: Path) -> "Judge":
        provider_name = config.get("provider", "anthropic")
        provider: Provider | None = None
        if provider_name == "anthropic":
            provider = anthropic_provider(base_url=config.get("base_url", "https://api.anthropic.com"))
        elif provider_name == "openai":
            provider = openai_provider(base_url=config.get("base_url", "https://api.openai.com/v1"))
        cassette = config.get("cassette")
        return cls(model=config.get("model", "claude-haiku-5-5"), provider=provider,
                   mode=config.get("mode", "auto"),
                   cassette=(base_dir / cassette) if cassette else None)

    def _key(self, model: str, system: str, user: str) -> str:
        return hashlib.sha256(json.dumps([model, system, user]).encode()).hexdigest()

    def ask(self, system: str, user: str, model: str | None = None) -> str:
        model = model or self.model
        key = self._key(model, system, user)
        start = time.perf_counter()
        entry = self._cache.get(key)
        if entry and self.mode != "live":
            record_call(model=model, prompt=user, output=entry["text"], input_tokens=entry.get("input_tokens", 0),
                        output_tokens=entry.get("output_tokens", 0), latency_ms=0.0, purpose="judge")
            return entry["text"]
        if self.mode == "replay":
            raise JudgeError("no recorded judge response for this input; run with judge mode 'auto' or 'live' "
                             "to record it, then commit the cassette")
        if self.provider is None:
            raise JudgeError("no judge provider configured")
        reply = self.provider(model, system, user)
        record_call(model=model, prompt=user, output=reply.text, input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens, latency_ms=(time.perf_counter() - start) * 1000,
                    purpose="judge")
        if self.cassette:
            record = {"key": key, "model": model, "text": reply.text,
                      "input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens}
            with self._lock:
                self._cache[key] = record
                self.cassette.parent.mkdir(parents=True, exist_ok=True)
                with self.cassette.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
        return reply.text


def parse_json_reply(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a judge reply (tolerates prose or code fences around it)."""
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise JudgeError(f"judge reply contained no JSON object: {text[:120]!r}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judge reply was not valid JSON: {exc.msg}") from exc
