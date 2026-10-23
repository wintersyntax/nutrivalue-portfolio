#!/usr/bin/env python3
"""TASK-056: build/extend a private page set for the promotion benchmark (no HTTP).

  # pages of a saved TASK-051 run
  ./scripts/ci-python scripts/prepare_page_set.py --set data/private/tjek/page-sets/W43-hot \\
      --from-run data/private/tjek/weekly/2026-W43/run-... --include with_hotspots --sample 30
  # ordered saved page images of one catalogue
  ... --from-images 'data/private/x/zoom-pages/page-*.jpg' --catalog-id FClkBYdb --chain "REMA 1000" \\
      --valid-from 2026-10-11T22:00:00+0000 --valid-to 2026-10-17T21:59:59+0000
  # an official PDF (needs: uv run --with pymupdf==1.27.2 ...)
  ... --from-pdf data/private/meny-flyers/Uke_42_mandag.pdf --catalog-id pdf-meny-2026-W42 ...
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.page_sets import (  # noqa: E402
    PageSetError, add_from_images, add_from_pdf, add_from_run,
)

PRIVATE = ROOT / "data" / "private"


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--set", type=Path, required=True, help="page-set directory under data/private/")
    src = cli.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-run", type=Path)
    src.add_argument("--from-images")
    src.add_argument("--from-pdf", type=Path)
    cli.add_argument("--include", default="all")
    cli.add_argument("--sample", type=int)
    cli.add_argument("--seed", type=int, default=0)
    for name in ("--catalog-id", "--chain", "--valid-from", "--valid-to", "--catalog-label"):
        cli.add_argument(name, default="" if name == "--catalog-label" else None)
    args = cli.parse_args(argv)
    target = args.set.resolve()
    if PRIVATE.resolve() not in target.parents:
        cli.error("--set must be inside data/private/")
    manifest = target / "manifest.json"
    try:
        if args.from_run:
            data = add_from_run(manifest, args.from_run, include=args.include,
                                sample=args.sample, seed=args.seed)
        else:
            missing = [n for n in ("catalog_id", "chain", "valid_from", "valid_to")
                       if not getattr(args, n)]
            if missing:
                cli.error("needs --" + ", --".join(m.replace("_", "-") for m in missing))
            common = dict(catalog_id=args.catalog_id, chain=args.chain,
                          valid_from=args.valid_from, valid_to=args.valid_to,
                          catalog_label=args.catalog_label)
            if args.from_pdf:
                data = add_from_pdf(manifest, args.from_pdf, **common)
            else:
                images = sorted(glob.glob(args.from_images))
                if not images:
                    cli.error("--from-images matched no files")
                data = add_from_images(manifest, images, source=args.from_images, **common)
    except (PageSetError, OSError, KeyError) as exc:
        print(f"RED: {exc}", file=sys.stderr)
        return 1
    print(f"Page set {target.name}: {len(data['pages'])} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
