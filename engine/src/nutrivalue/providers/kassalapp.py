"""Kassalapp API v1 client and conservative normalization for review-only staging.

Never infer an offer validity interval or nutrition measurement basis from a
product-search response. Parsed values are *candidates*, not verified rankings.
"""

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

BASE_URL = "https://kassal.app/api/v1"


class KassalappError(RuntimeError):
    """Safe, token-free exception for provider transport or payload errors."""


class KassalappRateLimit(KassalappError):
    """HTTP 429; pause requests rather than bypassing provider limits."""


class KassalappClient:
    def __init__(self, token: str, *, opener=urlopen):
        if not token or not token.strip():
            raise ValueError("KASSALAPP_API_KEY is required")
        self._token = token.strip()
        self._opener = opener

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get("KASSALAPP_API_KEY", ""))

    def _request(self, method: str, path: str, *, params=None, body=None):
        url = BASE_URL + path
        if params:
            url += "?" + urlencode(params)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(
            url,
            data=payload,
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
            method=method,
        )
        try:
            with self._opener(req, timeout=20) as resp:
                content = resp.read()
        except HTTPError as exc:
            if exc.code == 429:
                raise KassalappRateLimit("Kassalapp rate limit reached (429); wait before retrying") from None
            raise KassalappError(f"Kassalapp returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError):
            raise KassalappError("Kassalapp network request failed") from None
        try:
            document = json.loads(content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise KassalappError("Kassalapp returned invalid JSON") from exc
        if not isinstance(document, dict) or "data" not in document:
            raise KassalappError("Kassalapp response has no data field")
        return document

    def search_products(self, name: str, *, size: int = 20, page: int = 1):
        if not isinstance(name, str) or len(name.strip()) < 3:
            raise ValueError("Search must have at least three characters")
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 100:
            raise ValueError("size must be between 1 and 100")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValueError("page must be a positive integer")
        result = self._request(
            "GET", "/products", params={
                # Verified on 2026-10-10: the live API accepts these three
                # parameters, but adding exclude_without_ean=true returns 422.
                # Preserve no-EAN products for explicit manual review instead.
                "search": name.strip(), "size": size, "page": page,
            },
        )
        if not isinstance(result["data"], list):
            raise KassalappError("Product search data is not an array")
        return result

    def compare_by_ean(self, ean: str):
        if not ean.isdigit() or not 8 <= len(ean) <= 14:
            raise ValueError("Expected an 8–14 digit EAN/GTIN")
        return self._request("GET", "/products/ean/" + quote(ean, safe=""))

    def bulk_price_history(self, eans: list[str], *, days: int = 30, aggregation: str = "min"):
        if not 1 <= len(eans) <= 100 or any(not x.isdigit() for x in eans):
            raise ValueError("Provide 1–100 numeric EAN strings")
        if not 1 <= days <= 90:
            raise ValueError("Free-plan history window must be 1–90 days")
        if aggregation not in {"min", "max", "avg"}:
            raise ValueError("Unsupported aggregation")
        return self._request(
            "POST", "/products/prices-bulk",
            body={"eans": eans, "days": days, "aggregation": aggregation},
        )


@dataclass(frozen=True)
class StagedProduct:
    provider_product_id: str
    ean: str | None
    name: str
    brand: str | None
    store_code: str | None
    price_nok: str | None
    weight_g: str | None
    protein_g_unverified_basis: str | None
    review_status: str
    review_notes: str
    source_ref: str


def _positive_decimal(value, *, maximum=None):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not n.is_finite() or n <= 0 or (maximum is not None and n > maximum):
        return None
    return str(n)


def stage_product(record: dict) -> StagedProduct:
    """Normalize supported dimensions; preserve uncertainty rather than inventing data.

    A candidate is NOT verified: ProductResource does not establish an exact
    price-check time or protein nutrition basis. Product comparison remains
    separate until its multi-store shape is validated.
    """
    if not isinstance(record, dict) or not isinstance(record.get("id"), int):
        raise ValueError("Kassalapp product requires integer id")
    provider_id = str(record["id"])
    name = str(record.get("name") or "").strip()
    ean_raw = record.get("ean")
    ean = str(ean_raw).strip() if ean_raw is not None else None
    if not ean or not ean.isdigit() or not 8 <= len(ean) <= 14:
        ean = None
    brand = record.get("brand") if isinstance(record.get("brand"), str) else None
    store_field = record.get("store")
    if isinstance(store_field, dict):
        stores = [store_field]
    elif isinstance(store_field, list):
        stores = store_field
    else:
        stores = []
    store_code = (
        stores[0].get("code")
        if len(stores) == 1 and isinstance(stores[0], dict)
        and isinstance(stores[0].get("code"), str)
        else None
    )
    price = _positive_decimal(record.get("current_price"))
    weight = _positive_decimal(record.get("weight"))
    unit = record.get("weight_unit")
    weight_g = None
    if weight is not None and unit in ("g", "kg"):
        amount = Decimal(weight) * (Decimal("1000") if unit == "kg" else Decimal("1"))
        weight_g = str(amount)
    nutrition = record.get("nutrition")
    protein = None
    if isinstance(nutrition, list):
        matches = [
            item for item in nutrition
            if isinstance(item, dict) and item.get("code") == "protein"
            and item.get("unit") == "g"
        ]
        if len(matches) == 1:
            protein = _positive_decimal(matches[0].get("amount"), maximum=Decimal("100"))
    missing = [
        label for label, value in (
            ("name", name), ("ean", ean), ("store", store_code),
            ("price", price), ("weight_in_g_or_kg", weight_g), ("protein_in_g", protein),
        ) if value is None or value == ""
    ]
    # Even a complete shape cannot assert a 100-g nutrition basis, edible
    # net weight, current-price verification, or promotional validity.
    status = "incomplete" if missing else "needs_review"
    notes = (
        "missing_or_ambiguous:" + ",".join(missing)
        if missing else
        "requires_nutrition_basis_edible_mass_and_price_freshness_verification"
    )
    return StagedProduct(
        provider_product_id=provider_id, ean=ean, name=name, brand=brand,
        store_code=store_code, price_nok=price, weight_g=weight_g,
        protein_g_unverified_basis=protein, review_status=status,
        review_notes=notes,
        source_ref=f"{BASE_URL}/products/id/{provider_id}",
    )
