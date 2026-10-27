"""Deterministic, unit-conscious protein economics."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class ProteinValue:
    total_protein_g: Decimal
    nok_per_g_protein: Decimal


def _decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return number


def protein_value(
    price_nok: object,
    package_net_edible_g: object,
    protein_per_100g_as_sold: object,
) -> ProteinValue:
    """Return price efficiency only for matched as-sold/edible values.

    Input provenance and measurement-state checks belong to the caller; this
    function does not infer edible yield or convert cooked into raw values.
    """
    price = _decimal(price_nok, "price_nok")
    mass = _decimal(package_net_edible_g, "package_net_edible_g")
    protein = _decimal(protein_per_100g_as_sold, "protein_per_100g_as_sold")
    if price <= 0 or mass <= 0 or protein <= 0 or protein > 100:
        raise ValueError("positive price/mass and protein in (0, 100] are required")
    total = mass * protein / Decimal("100")
    return ProteinValue(
        total_protein_g=total,
        nok_per_g_protein=price / total,
    )
