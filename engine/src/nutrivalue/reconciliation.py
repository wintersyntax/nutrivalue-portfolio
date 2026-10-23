"""TASK-020 offline candidate comparison; never auto-confirm retail price or nutrition.

No network, source mutation, guess-based EAN assignment, or product-name joins.
Outputs are review-only even when all candidate fields agree.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


@dataclass(frozen=True)
class Identity:
    ean: str | None
    brand: str | None
    variant: str | None
    package_quantity: str | None
    package_unit: str | None
    pack_count: int | None = None


def _normal(value: str | None) -> str | None:
    return ' '.join(value.casefold().split()) if isinstance(value, str) and value.strip() else None


def _net_amount(value: str | None, unit: str | None, count: int | None):
    if value is None or unit is None or (count is not None and (type(count) is not int or count < 1)):
        return None
    try:
        amount=Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount<=0:
        return None
    mapping={'g':('g',1),'kg':('g',1000),'ml':('ml',1),
             'l':('ml',1000),'liter':('ml',1000),'stk':('stk',1)}
    if unit not in mapping:
        return None
    basis,factor=mapping[unit]
    return (basis,amount*factor*(count or 1))


def compare_candidates(retailer: Identity, external: Identity) -> dict:
    """An external EAN alone is never retailer-card identity attestation."""
    missing=[]
    conflicts=[]
    for field in ('ean','brand','variant'):
        a=_normal(getattr(retailer,field))
        b=_normal(getattr(external,field))
        if not a or not b:
            missing.append(field+'_not_corroborated')
        elif a!=b:
            conflicts.append(field+'_conflict')
    pack_a=_net_amount(retailer.package_quantity,retailer.package_unit,retailer.pack_count)
    pack_b=_net_amount(external.package_quantity,external.package_unit,external.pack_count)
    if pack_a is None or pack_b is None:
        missing.append('package_not_corroborated')
    elif pack_a!=pack_b:
        conflicts.append('package_conflict')
    status=('conflicting_evidence' if conflicts else
            'needs_manual_review' if missing else 'candidate_match')
    return {'status':status,'reasons':sorted(missing+conflicts),
            'confirmed_identity':False,'confirmed_package':False,
            'verified_price':False,'verified_nutrition':False}


def derived_unit_price_ore(price_ore: int, quantity: str | None,
                           unit: str | None, pack_count: int | None,
                           *, price_basis='per_package', deposit_applies=False,
                           target_unit='kg', source_unit_ore: int | None=None):
    """Return (ø/l or ø/kg, diagnostic); not independently verified retail price.

    For multi-packs quantity is per-item and pack_count is number of items.
    Refuse to guess liter-to-kilo density or remove unknown pant from price.
    """
    if type(price_ore) is not int or price_ore<=0 or price_basis!='per_package':
        return None,'invalid_or_unknown_price_basis'
    if deposit_applies:
        return None,'deposit_condition_unresolved'
    pack=_net_amount(quantity,unit,pack_count)
    if not pack:
        return None,'package_denominator_unresolved'
    basis,total=pack
    if (basis,target_unit) not in (('g','kg'),('ml','l'),('stk','stk')):
        return None,'incompatible_units_or_unknown_density'
    amount=total/Decimal(1000 if basis in ('g','ml') else 1)
    rate=int((Decimal(price_ore)/amount).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    if source_unit_ore is not None:
        if type(source_unit_ore) is not int or source_unit_ore<=0:
            return None,'invalid_source_rate'
        if abs(rate-source_unit_ore)>1:
            return rate,'source_unit_rate_mismatch_review'
    return rate,'derived_source_only_not_verified'
