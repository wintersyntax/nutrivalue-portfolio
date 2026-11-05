"""TASK-021: conservative five-real-card offline pilot across PRIVATE source-only DBs.

Reads a specified existing REMA snapshot and existing Kassalapp staging only.
No API calls, matching by loose food categories, checked shelf prices, verified
nutrition, writable source database handles or automatic curated promotion.
Name comparisons are negative-scope audits, not verified SKU matches.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from nutrivalue.reconciliation import derived_unit_price_ore

REMA_SOURCE = 'https://www.rema.no/kampanjer/last-pris/'
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
LOOKUP_SCOPE = 'local_staging_exact_name_only'


def _canon(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _normal(name: str) -> str:
    # Unicode-aware: ø, æ, å casefold; no fuzzy joins or mistaken category EAN.
    return ' '.join(name.casefold().split())


def _db_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError('missing private SQLite input: ' + path.name)
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


def _mass_g(quantity, unit, pack_count):
    if quantity is None or unit is None:
        return None
    try:
        q = Decimal(quantity)
        if not q.is_finite() or q<=0:
            return None
        if pack_count is not None:
            if type(pack_count) is not int or pack_count < 1:
                return None
            q *= pack_count
        if unit=='kg':
            q *= 1000
        elif unit!='g':
            return None
        return q
    except (InvalidOperation, TypeError, ValueError):
        return None


def _card_report(row, duplicates, candidate_rows, seen):
    candidates = [x for x in candidate_rows
                  if _normal(x['product_name']) == _normal(row['product_name'])]
    reasons = ['retailer_ean_missing', 'retailer_brand_variant_not_attested',
               'exact_branded_nutrition_not_checked',
               'independent_store_date_price_not_checked']
    mass = _mass_g(row['package_quantity'], row['package_unit'], row['pack_count'])
    if mass is None:
        reasons.append('package_net_mass_unresolved')
    if not candidates:
        reasons.append('no_exact_title_candidate_in_existing_kassalapp_staging')
    if duplicates:
        reasons.append('other_cards_share_visible_fields_identity_unresolved')
    conflicts = []
    for match in candidates:
        value = match['reported_weight_g']
        if mass is not None and value is not None:
            try:
                w = Decimal(str(value))
                if not w.is_finite() or w <= 0 or w != mass:
                    conflicts.append(match['id'])
            except (InvalidOperation, ValueError, TypeError):
                conflicts.append(match['id'])
    if conflicts:
        reasons.append('candidate_package_mass_conflict')
    target = ('kg' if row['package_unit'] in ('g','kg') else
              'l' if row['package_unit'] in ('ml','l','liter') else None)
    derived, diagnostic = (derived_unit_price_ore(
        row['price_ore'],row['package_quantity'],row['package_unit'],
        row['pack_count'],price_basis=row['price_basis'],
        target_unit=target or 'kg',
        source_unit_ore=(row['unit_price_ore'] if row['unit_price_unit']==target else None)
    ) if target else (None,'unsupported_or_unknown_unit_basis'))
    if diagnostic=='source_unit_rate_mismatch_review':
        reasons.append('published_vs_derived_unit_rate_conflict')
    if diagnostic not in ('derived_source_only_not_verified',
                          'source_unit_rate_mismatch_review'):
        reasons.append('derived_unit_price_unavailable')
    return {
        'card_no':row['card_no'], 'product_name':row['product_name'],
        'price_ore':row['price_ore'], 'price_basis':row['price_basis'],
        'package_quantity':row['package_quantity'],
        'package_unit':row['package_unit'],
        'reported_unit_price_ore':row['unit_price_ore'],
        'derived_unit_price_ore':derived,
        'derived_unit_status':diagnostic,
        'candidate_staging_ids':[x['id'] for x in candidates],
        'same_visible_fields_card_numbers':duplicates,
        'lookup':{'provider':'kassalapp_staging',
                  'scope':'exact_normalized_name_in_existing_local_rows',
                  'source_rows_scanned':seen,
                  'matching_rows':len(candidates),
                  'result':('name_candidate_not_verified' if candidates
                            else 'no_exact_name_in_staging')},
        'result':('conflicting_evidence' if conflicts or
                  diagnostic=='source_unit_rate_mismatch_review'
                  else 'insufficient_evidence'),
        'reasons':sorted(set(reasons)),
        'source_only':True,
        'identity_confirmed':False, 'nutrition_verified':False,
        'local_retail_price_verified':False,
    }


def inspect(rema_path: Path, kassalapp_path: Path, snapshot_sha: str,
            card_nos: tuple[int, ...]) -> dict:
    if not isinstance(snapshot_sha,str) or SHA_RE.fullmatch(snapshot_sha) is None:
        raise ValueError('exact 64-character REMA snapshot SHA-256 required')
    if not isinstance(card_nos,tuple) or len(card_nos)!=5 or \
       any(type(x) is not int or x<1 for x in card_nos) or len(set(card_nos))!=5:
        raise ValueError('exactly five distinct, positive retailer card numbers required')
    if rema_path.resolve()==kassalapp_path.resolve():
        raise ValueError('REMA and Kassalapp evidence must be separate private DBs')
    with closing(_db_read_only(rema_path)) as rem, closing(_db_read_only(kassalapp_path)) as kas:
        source=rem.execute(
            'SELECT sha256,source_url,fetched_at FROM rema_locked_snapshots '
            'WHERE sha256=?',(snapshot_sha,)).fetchone()
        if source is None or source['source_url']!=REMA_SOURCE:
            raise ValueError('required exact REMA snapshot absent or wrong source')
        placeholders=','.join('?' for _ in card_nos)
        selected=rem.execute(
            'SELECT * FROM rema_locked_cards WHERE snapshot_sha256=? '
            f'AND card_no IN ({placeholders})',(snapshot_sha,*card_nos)
        ).fetchall()
        if len(selected)!=5 or any(x['review_state']!='source_only'
                                   for x in selected):
            raise ValueError('selected REMA cards missing or not source_only')
        all_cards=rem.execute(
            'SELECT card_no,product_name,price_ore,package_quantity,'
            'package_unit,pack_count FROM rema_locked_cards '
            'WHERE snapshot_sha256=?',(snapshot_sha,)).fetchall()
        provider=kas.execute(
            'SELECT id,ean,product_name,brand,reported_weight_g,'
            'source_ref,fetched_at,review_status FROM kassalapp_staging '
            'ORDER BY id',( )).fetchall()
        if not provider:
            raise ValueError('Kassalapp staging is empty; cannot claim lookup attempt')
        observations=[]
        for row in sorted(selected,key=lambda x:x['card_no']):
            duplicates=sorted(x['card_no'] for x in all_cards
              if x['card_no']!=row['card_no'] and
              _normal(x['product_name'])==_normal(row['product_name']) and
              x['price_ore']==row['price_ore'] and
              x['package_quantity']==row['package_quantity'] and
              x['package_unit']==row['package_unit'] and
              x['pack_count']==row['pack_count'])
            observations.append(_card_report(row,duplicates,provider,len(provider)))
        lineage=[(p['id'],p['ean'],p['product_name'],p['brand'],
                  p['reported_weight_g'],p['source_ref'],p['fetched_at'],
                  p['review_status']) for p in provider]
    stable={
        'kind':'private_five_card_rema_kassalapp_review',
        'rema_snapshot_sha256':snapshot_sha,
        'rema_source_url':source['source_url'],
        'rema_fetched_at':source['fetched_at'],
        'kassalapp_staging_rows_seen':len(provider),
        'lookup_scope':LOOKUP_SCOPE,
        'cases':observations,
        'staging_fingerprint_sha256':hashlib.sha256(_canon(lineage).encode()).hexdigest(),
        'status':'source_only',
        'manual_external_lookups':'not_attempted',
    }
    stable['run_sha256']=hashlib.sha256(_canon(stable).encode()).hexdigest()
    return stable


def store(report: dict, output_path: Path, schema_path: Path,
          private_root: Path) -> tuple[int,int]:
    root=private_root.resolve()
    target=output_path.resolve()
    if target.suffix!='.sqlite3' or not target.is_relative_to(root):
        raise ValueError('pilot output must be .sqlite3 under ignored data/private')
    if len(report['cases'])!=5 or not SHA_RE.fullmatch(report['run_sha256']):
        raise ValueError('invalid pilot report')
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(target) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(schema_path.read_text(encoding='utf-8'))
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute('SELECT 1 FROM pilot_runs WHERE run_sha256=?',
                            (report['run_sha256'],)).fetchone()
        if existing:
            return 0,5
        when=datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO pilot_runs VALUES (?,?,?,?,?,?,?,?)',(
            report['run_sha256'],report['rema_snapshot_sha256'],
            report['rema_fetched_at'],report['rema_source_url'],
            report['kassalapp_staging_rows_seen'],when,
            LOOKUP_SCOPE,'source_only'
        ))
        for c in report['cases']:
            db.execute('INSERT INTO pilot_cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                report['run_sha256'],c['card_no'],c['product_name'],
                c['price_ore'],c['package_quantity'],c['package_unit'],
                c['price_basis'],c['reported_unit_price_ore'],
                c['derived_unit_price_ore'],c['derived_unit_status'],
                c['result'],_canon(c['reasons']),
                _canon(c['same_visible_fields_card_numbers']),
                _canon(c['candidate_staging_ids']),1
            ))
            a=c['lookup']
            db.execute('INSERT INTO pilot_lookup_attempts VALUES (?,?,?,?,?,?,?,?)',(
                report['run_sha256'],c['card_no'],a['provider'],a['scope'],
                a['source_rows_scanned'],a['matching_rows'],a['result'],when
            ))
    return 5,0
