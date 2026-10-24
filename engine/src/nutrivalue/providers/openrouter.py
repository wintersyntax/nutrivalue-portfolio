"""TASK-053: minimal OpenRouter client for bounded, owner-run vision calls.

The API key is read ONLY from the OPENROUTER_API_KEY environment variable by
the caller and is never written, printed or returned. One request per call:
no retries, no fallbacks, no redirects to other hosts.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "https://openrouter.ai/api/v1"
MAX_RESPONSE = 2_000_000


KEYCHAIN_SERVICE = "nutrivalue-openrouter"


def api_key(environ=None, platform: str | None = None, run=subprocess.run) -> str:
    """OPENROUTER_API_KEY from the environment, else (macOS) from the login Keychain.

    Store it once with:
      security add-generic-password -a "$USER" -s nutrivalue-openrouter -w
    (prompts without echo). Never printed, logged or written anywhere.
    """
    environ = os.environ if environ is None else environ
    key = (environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key
    if (platform or sys.platform) == "darwin":
        try:
            done = run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                       capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return ""
        if done.returncode == 0:
            return done.stdout.strip()
    return ""


class OpenRouterError(RuntimeError):
    """A call failed; the message never contains the key or the request body."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _read(request, opener, timeout):
    try:
        with opener(request, timeout=timeout) as response:
            if response.status != 200:
                raise OpenRouterError(f"OpenRouter HTTP {response.status}; no retry",
                                      response.status)
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        raise OpenRouterError(f"OpenRouter HTTP {exc.code}; no retry", exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OpenRouterError("OpenRouter network/timeout error; no retry") from None
    if len(raw) > MAX_RESPONSE:
        raise OpenRouterError("OpenRouter response exceeded byte limit")
    try:
        return json.loads(raw)
    except ValueError:
        raise OpenRouterError("OpenRouter returned invalid JSON") from None


def fetch_models(*, opener=urllib.request.urlopen, timeout: int = 30) -> dict:
    """Public model catalogue: id -> pricing, supported parameters, modalities."""
    request = urllib.request.Request(f"{BASE}/models", headers={"Accept": "application/json"})
    data = _read(request, opener, timeout).get("data", [])
    models = {}
    for item in data:
        pricing = item.get("pricing") or {}
        try:
            prices = (float(pricing.get("prompt", "nan")), float(pricing.get("completion", "nan")))
        except (TypeError, ValueError):
            continue
        models[item.get("id")] = {
            "prompt_usd_per_token": prices[0],
            "completion_usd_per_token": prices[1],
            "supported_parameters": list(item.get("supported_parameters") or []),
            "input_modalities": list((item.get("architecture") or {}).get("input_modalities") or []),
        }
    return models


# TASK-056 routing guardrail. Per chosen model: provider allowlist in order of
# preference and a price ceiling (USD per million tokens) at the model's base
# list price, so OpenRouter never routes to a pricier regional or "fast"
# endpoint. require_parameters keeps away hosts that would silently ignore the
# strict JSON schema; data_collection=deny excludes hosts that keep prompts.
ROUTES = {
    "anthropic/claude-haiku-5.5": {"order": ["anthropic", "azure", "google-vertex"],
                                   "max_price": {"prompt": 0.10, "completion": 0.50}},
    "openai/gpt-6-luna": {"order": ["openai", "azure"],
                          "max_price": {"prompt": 0.10, "completion": 0.50}},
}


def provider_policy(model: str, info: dict | None = None) -> dict:
    """OpenRouter ``provider`` object for one model (same model only, never another)."""
    policy = {"allow_fallbacks": True, "require_parameters": True, "data_collection": "deny"}
    route = ROUTES.get(model)
    if route is not None:
        policy["order"] = list(route["order"])
        policy["only"] = list(route["order"])
        policy["max_price"] = dict(route["max_price"])
    elif info is not None:      # benchmark candidates: listed price is the ceiling
        policy["sort"] = "price"
        policy["max_price"] = {"prompt": round(info["prompt_usd_per_token"] * 1e6, 6),
                               "completion": round(info["completion_usd_per_token"] * 1e6, 6)}
    return policy


# Providers that cache prompts automatically bill a first-time prompt as a
# cache write (OpenAI/Azure GPT-6 Luna: 0.125 vs 0.10 USD/M, measured on every
# W42/W43 first-pass call); 1.25x is also the usual Anthropic write premium.
CACHE_WRITE_FACTOR = 1.25


def price_ceiling_usd(policy: dict, prompt_tokens, completion_tokens) -> float | None:
    """Most a call may cost under the policy's max_price (prompt tokens allowed
    at the cache-write rate); None when not checkable."""
    ceiling = policy.get("max_price")
    if not ceiling or not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        return None
    return (prompt_tokens * ceiling["prompt"] * CACHE_WRITE_FACTOR
            + completion_tokens * ceiling["completion"]) / 1e6


class OpenRouterClient:
    def __init__(self, api_key: str, *, opener=urllib.request.urlopen, timeout: int = 120):
        if not isinstance(api_key, str) or len(api_key) < 20:
            raise OpenRouterError("OpenRouter key missing or malformed: set OPENROUTER_API_KEY or "
                                  f"store it in the Keychain as {KEYCHAIN_SERVICE!r}")
        self._key = api_key
        self._opener = opener
        self._timeout = timeout

    def complete(self, model: str, prompt: str, image: bytes, schema: dict, *,
                 structured: bool, temperature: bool, max_tokens: int = 4000,
                 provider: dict | None = None) -> dict:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")}},
            ]}],
            "max_tokens": max_tokens,
            "usage": {"include": True},
            # Same model only: OpenRouter may route to another host of THIS
            # model (within the policy) when one is rate limited; the
            # answering provider is saved.
            "provider": provider or {"allow_fallbacks": True},
        }
        if structured:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "page_promotions", "strict": True, "schema": schema}}
        else:
            body["response_format"] = {"type": "json_object"}
        if temperature:
            body["temperature"] = 0
        request = urllib.request.Request(
            f"{BASE}/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                     "X-Title": "NutriValue private benchmark"})
        started = time.monotonic()
        data = _read(request, self._opener, self._timeout)
        latency = time.monotonic() - started
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None
        usage = data.get("usage") or {}
        return {"content": content, "cost_usd": usage.get("cost"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "provider": data.get("provider"), "model": data.get("model"),
                "latency_s": round(latency, 2)}
