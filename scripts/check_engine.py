#!/usr/bin/env python3
"""Run public engine contracts; explicitly report private-only archive tests.

The four excluded modules refer to deliberately unpublished deployment or
archive inputs. They remain available as source; this is not a full private
engine test run. No provider calls or credentials are needed.
"""
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
EXCLUDED = {
    "test_promotion_pipeline": "unpublished standing-benefit registry",
    "test_weekly_cloud": "unpublished cloud job entry point and registry",
    "test_rema_flyer_archive": "unpublished source manifest and archive policy",
    "test_sequential_workflow": "private CI workflows and operator scripts",
}


def main():
    for path in (ENGINE, ENGINE / "src", ENGINE / "tests"):
        sys.path.insert(0, str(path))
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for path in sorted((ENGINE / "tests").glob("test_*.py")):
        if path.stem in EXCLUDED:
            print(f"EXCLUDED {path.stem}: {EXCLUDED[path.stem]}", flush=True)
            continue
        suite.addTests(loader.loadTestsFromName(path.stem))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
