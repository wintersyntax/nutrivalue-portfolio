#!/usr/bin/env python3
"""TASK-055: open the weekly promotion review in the browser (local only).

  ./scripts/ci-python scripts/review_promotions_web.py          # http://127.0.0.1:8765
  ./scripts/ci-python scripts/review_promotions_web.py --port 8800 --no-browser

The page shows the queue (accept / reject / page checked, keyboard A/R/C,
arrows), spot-check lists and per-chain counts. It reads and writes ONLY the
private offer database and serves saved page images; no network calls.
Stop it with Ctrl-C.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.promotion_review_web import serve  # noqa: E402

PRIVATE = ROOT / "data" / "private"


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--db", type=Path, default=PRIVATE / "offers.sqlite3")
    cli.add_argument("--weekly-root", type=Path, default=PRIVATE / "tjek" / "weekly")
    cli.add_argument("--port", type=int, default=8765)
    cli.add_argument("--no-browser", action="store_true")
    args = cli.parse_args(argv)
    db = args.db.resolve()
    if PRIVATE.resolve() not in db.parents:
        cli.error("--db must be inside data/private/")
    if not db.is_file():
        cli.error("offer database not found; import a week first")
    if not 1024 <= args.port <= 65535:
        cli.error("--port must be between 1024 and 65535")
    server = serve(db, ROOT / "analytics", args.weekly_root.resolve(), args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Promotion review: {url}  (Ctrl-C to stop)")
    if not args.no_browser and sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
