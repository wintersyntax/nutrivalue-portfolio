"""TASK-055: permanent loyalty benefits ("Alltid 10 % bonus ...").

They are printed every week, so a reviewed per-chain registry
(``data/reference/standing_benefits.json``) recognises them instead of
sending them to the review queue. A match needs chain, kind and value and,
when the entry lists ``scope_keywords``, one of those words in the scope
(compared without accents, so Ånglamark = Änglamark). The registry's member
flag replaces the models' guess.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

KINDS = ("n_for_m", "percent_off", "amount_off", "bonus_percent")


class StandingRegistryError(ValueError):
    """The registry file is malformed."""


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text).casefold())
    folded = folded.replace("ø", "o").replace("æ", "ae")
    return "".join(c for c in folded if not unicodedata.combining(c))


def load_registry(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("benefits") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise StandingRegistryError("registry needs a benefits list")
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or entry.get("kind") not in KINDS
                or entry["kind"] == "n_for_m"
                or not isinstance(entry.get("value"), (int, float))
                or not isinstance(entry.get("chains"), list) or not entry["chains"]
                or type(entry.get("member_only")) is not bool
                or not isinstance(entry.get("id"), str) or entry["id"] in seen):
            raise StandingRegistryError(f"bad registry entry: {entry!r}"[:200])
        seen.add(entry["id"])
    return entries


def _value(promo: dict) -> float | None:
    if promo["kind"] == "amount_off":
        return promo.get("amount_nok")
    return promo.get("percent")


def match(promo: dict, chain: str, registry: list[dict]) -> dict | None:
    """The registry entry this promotion is, or None."""
    for entry in registry:
        if chain not in entry["chains"] or promo["kind"] != entry["kind"]:
            continue
        value = _value(promo)
        if value is None or abs(float(value) - float(entry["value"])) > 1e-6:
            continue
        words = entry.get("scope_keywords")
        if words and not any(_plain(w) in _plain(promo.get("scope", "")) for w in words):
            continue
        return entry
    return None


def normalize(parsed: dict | None, chain: str, registry: list[dict]) -> dict | None:
    """Copy of a parsed answer with standing items tagged and member flag fixed.

    A standing benefit repeated on one page (e.g. the KIWI 15 % badge on every
    vegetable) is kept once, with the registry's wording as scope.
    """
    if parsed is None:
        return None
    items, seen = [], set()
    for promo in parsed["promotions"]:
        entry = match(promo, chain, registry)
        promo = dict(promo)
        if entry is not None:
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            promo.update(standing_id=entry["id"], member_only=entry["member_only"],
                         scope=entry["scope"])
        items.append(promo)
    return {**parsed, "promotions": items}
