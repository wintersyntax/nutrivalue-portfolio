#!/usr/bin/env python3
"""TASK-032: geographic Extra catalogue resolution at public reference points.

No HTTP by default. --replay-dir reuses the owner's existing private six-call
probe, or --live (explicit opt-in) makes <=2 Tjek /v2/catalogs GETs, Oslo and
Bergen public centres. No offers, SQLite writes, or private home coordinates.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_geo_region import (  # noqa: E402
    POINTS, EXTRA, replay_geo, capture_geo,
)
from nutrivalue.providers.tjek_weekly_collect import CollectionError, live_json  # noqa: E402

ARCHIVE = ROOT / "data/private/tjek/weekly"


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--replay-dir", type=Path,
                      help="offline archived extra-api-investigation folder (0 GETs)")
    mode.add_argument("--live", action="store_true",
                      help="explicitly permit 2 geographic catalog metadata GETs")
    cli.add_argument("--as-of", default=None,
                     help="ISO timestamp with timezone (Europe/Oslo now by default)")
    args = cli.parse_args(argv)
    as_of = args.as_of or datetime.now(ZoneInfo("Europe/Oslo")).isoformat()
    if not args.live and args.replay_dir is None:
        print("TJEK EXTRA GEOGRAPHIC PLAN — ZERO NETWORK / ZERO WRITES")
        print(f"Dealer: {EXTRA}; reference points: " +
              ", ".join(f"{name}={POINTS[name]}" for name in ("oslo", "bergen")))
        print("Replay existing saved responses with --replay-dir.")
        print("Future refresh: --live = maximum 2 serial catalog GETs/week.")
        print("A geographic catalog candidate is NOT a verified shop shelf price.")
        return 0
    try:
        if args.replay_dir is not None:
            summary = replay_geo(private_root=ARCHIVE, input_dir=args.replay_dir,
                                 as_of=as_of)
            result = summary
        else:
            summary = capture_geo(
                private_root=ARCHIVE, as_of=as_of,
                get_json=lambda ep, params: live_json(ep, params),
            )
            result = summary["resolution"]
    except (CollectionError, OSError) as exc:
        print(f"TJEK EXTRA GEO: RED — {exc}", file=sys.stderr)
        return 1
    print(f"TJEK EXTRA GEO: {summary['status']}; HTTP GETs={summary['http_requests']}")
    if result:
        print(f"Oslo local candidates: {result['oslo_active_local_candidates']}; "
              f"Bergen local candidates: {result['bergen_active_local_candidates']}")
        print(f"Shared catalogue IDs in both raw responses: "
              f"{result['shared_catalog_ids_in_responses']}")
        print(f"Oslo Extra catalog: {result['oslo_candidate_catalog_id'] or 'UNRESOLVED'}")
        print(f"Bergen control: {result['bergen_control_catalog_id'] or 'UNRESOLVED'}")
        print("Scope: geographic Tjek catalog at public city point, NOT a validated store price.")
    if summary.get("halted_reason"):
        print(f"HALTED: {summary['halted_reason']}")
    print("Private evidence only; no offer GET, no cloud, no SQLite updates.")
    return 0 if result and result["status"] == "geo_oslo_catalog_candidate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
