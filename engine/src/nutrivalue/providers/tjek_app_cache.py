"""TASK-034A: bounded, explicitly opted-in Norwegian app-cache diagnostics.

No credential discovery, retries, redirects, persistence or DB mutations.
Raw values are returned only to the in-memory diagnostic consumer.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import urllib.error
import urllib.request

from nutrivalue.providers.tjek_weekly_collect import _safe_id

URL = "https://etilbudsavis.no/"
MAX_RESPONSE_BYTES = 1_500_000
MAX_BATCH_OFFERS = 5
MAX_REQUESTS = 6
MAX_PRODUCTS = 500
KINDS = ("offer", "offerProducts")


class AppCacheError(ValueError):
    """Only locally controlled error codes may reach reports."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AppCacheError("redirect")


def _object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise AppCacheError("malformed_ndjson")
        result[name] = value
    return result


def _constant(value):
    raise AppCacheError("malformed_ndjson")


def strict_json(raw):
    """Reject ambiguous keys and non-JSON constants without payload errors."""
    try:
        return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AppCacheError("malformed_ndjson") from None


def encode_cache_key(value):
    """Encode JSON as UTF-8 before Base64, including non-ASCII strings."""
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AppCacheError("invalid_cache_key") from None
    if len(raw) > 4096:
        raise AppCacheError("invalid_cache_key")
    return base64.b64encode(raw).decode("ascii")


def _decoded_key(encoded):
    try:
        if not isinstance(encoded, str) or len(encoded) > 8192:
            raise ValueError
        value = strict_json(base64.b64decode(encoded, validate=True).decode("utf-8"))
        if (not isinstance(value, list) or len(value) != 2
                or value[0] not in KINDS or not isinstance(value[1], dict)
                or set(value[1]) != {"publicId"}):
            raise ValueError
        return value[0], _safe_id(value[1]["publicId"])
    except (ValueError, TypeError, UnicodeError):
        raise AppCacheError("invalid_response_key") from None


def _failure(code):
    return {"status": "error", "value": None, "error": code}


def parse_cache_response(raw, wanted):
    """Read NDJSON atomically and map semantic cache keys, never line order.

    Missing replies and per-key errors remain explicit. Protocol ambiguity
    invalidates the batch; no partial success is published after bad JSON.
    """
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RESPONSE_BYTES:
        raise AppCacheError("response_size")
    wanted = list(wanted)
    if not 0 < len(wanted) <= MAX_BATCH_OFFERS * 2 or len(set(wanted)) != len(wanted):
        raise AppCacheError("invalid_queries")
    try:
        lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    except UnicodeError:
        raise AppCacheError("malformed_ndjson") from None
    if len(lines) > len(wanted):
        raise AppCacheError("unexpected_responses")
    result = {}
    for line in lines:
        row = strict_json(line)
        if not isinstance(row, dict):
            raise AppCacheError("malformed_ndjson")
        pair = _decoded_key(row.get("key"))
        if pair not in wanted or pair in result:
            raise AppCacheError("unexpected_response_key")
        if row.get("status") != "success":
            result[pair] = _failure("api_status")
            continue
        if "value" not in row:
            result[pair] = _failure("missing_value")
            continue
        value = row["value"]
        if pair[0] == "offer":
            valid = (isinstance(value, dict) and value.get("publicId") == pair[1]
                     and value.get("currencyCode") == "NOK")
        else:
            valid = (isinstance(value, list) and len(value) <= MAX_PRODUCTS
                     and all(isinstance(product, dict) for product in value))
        result[pair] = ({"status": "success", "value": value, "error": None}
                        if valid else _failure("invalid_value"))
    for pair in wanted:
        result.setdefault(pair, _failure("missing_response"))
    return result


class AppCacheClient:
    def __init__(self, token, *, live=False, opener=None):
        if (not isinstance(token, str) or not token.strip() or len(token) > 4096
                or any(ord(char) < 32 or ord(char) > 126 for char in token)):
            raise AppCacheError("missing_or_invalid_credentials")
        self._token = token.strip()
        self._live = live is True
        self._opener = opener
        self.requests = 0

    @classmethod
    def from_environment(cls, *, live=False):
        # The operator supplies an existing authorized key outside source.
        # Do not inspect browser storage, scripts, cookies or external keys.
        return cls(os.environ.get("ETILBUDSAVIS_API_KEY", ""), live=live)

    def fetch_offers(self, offer_ids):
        if not self._live:
            raise AppCacheError("live_not_enabled")
        if not isinstance(offer_ids, (list, tuple)) or len(offer_ids) > 60:
            raise AppCacheError("invalid_queries")
        try:
            ids = list(dict.fromkeys(_safe_id(oid) for oid in offer_ids))
        except (ValueError, TypeError):
            raise AppCacheError("invalid_queries") from None
        if not 0 < len(ids) <= MAX_BATCH_OFFERS:
            raise AppCacheError("batch_size")
        if self.requests >= MAX_REQUESTS:
            raise AppCacheError("request_budget")
        pairs = [(kind, oid) for oid in ids for kind in KINDS]
        payload = {"data": [encode_cache_key([kind, {"publicId": oid}])
                            for kind, oid in pairs]}
        request = urllib.request.Request(
            URL, method="POST", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "*/*",
                     "x-api-key": self._token,
                     "User-Agent": "NutriValue-personal-Tjek-identifier-diagnostic/0.1"})
        opener = self._opener or urllib.request.build_opener(NoRedirect()).open
        self.requests += 1
        try:
            with opener(request, timeout=12) as response:
                if response.status != 200:
                    raise AppCacheError(f"http_{int(response.status)}")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise AppCacheError(f"http_{exc.code}") from None
        except TimeoutError:
            raise AppCacheError("timeout") from None
        except urllib.error.URLError as exc:
            raise AppCacheError("timeout" if isinstance(exc.reason, TimeoutError)
                                else "network") from None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if isinstance(exc, AppCacheError):
                raise
            raise AppCacheError("network") from None
        return parse_cache_response(raw, pairs)
