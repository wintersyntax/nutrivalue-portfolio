#!/usr/bin/env python3
"""Offline publication guard; heuristics supplement review and a secret scanner.

Diagnostics contain paths and rule names, never matching values. Unknown file
formats, binaries, symlinks and unreviewed datasets fail closed.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".sql", ".md", ".json", ".yml", ".yaml", ".sh", ".txt", ".toml"}
TEXT_NAMES = {".gitignore", "LICENSE", "Dockerfile", "Makefile"}
REFERENCE_DATA = {"engine/data/reference/food_roles.json"}
SCREENSHOT = "assets/promotion-review-interface.png"
SCREENSHOT_BLOB = "0aad1d0be2b35cc1fa6cc987f66be7f919dbb646"
CHECKS = (
    ("private key marker", r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("GitHub token", "gh" + r"[pousr]_[A-Za-z0-9]{35,}"),
    ("GitHub fine-grained token", "github" + r"_pat_[A-Za-z0-9_]{40,}"),
    ("Google API key", "AI" + r"za[0-9A-Za-z_-]{35}"),
    ("AWS access key", "AK" + r"IA[0-9A-Z]{16}"),
    ("absolute home path", r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    ("owner-linked hosted instance", r"https?://[a-z0-9.-]+\.(?:pikapod\.net|run\.app)\b"),
    ("URL containing login credentials", r"https?://[^\s/]+:[^\s/]+@"),
)


def check_release() -> int:
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    errors = []
    for item in filter(None, files):
        path = Path(item)
        target = ROOT / path
        parts = {p.lower() for p in path.parts}
        if parts & {".venv", "venv", ".secrets", "secrets", "__pycache__", "private", "raw", "downloads"}:
            errors.append((item, "forbidden data or environment folder"))
        if "data" in parts and item not in REFERENCE_DATA:
            errors.append((item, "unreviewed dataset"))
        if path.name == ".env" or path.name.startswith(".env.") or "credentials" in path.name.lower():
            errors.append((item, "environment/credential path"))
        if target.is_symlink():
            errors.append((item, "symlink requires independent publication review"))
            continue
        if not target.is_file():
            errors.append((item, "tracked file missing from working tree"))
            continue
        if item == SCREENSHOT:
            actual = subprocess.check_output(["git", "hash-object", "--", item], cwd=ROOT, text=True).strip()
            if actual != SCREENSHOT_BLOB:
                errors.append((item, "approved synthetic screenshot changed"))
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            errors.append((item, "unreviewed file format or binary"))
            continue
        try:
            data = target.read_text(encoding="utf-8")
        except UnicodeError:
            errors.append((item, "non-UTF-8 content"))
            continue
        if "\0" in data:
            errors.append((item, "binary content"))
        for label, pattern in CHECKS:
            if re.search(pattern, data, re.IGNORECASE):
                errors.append((item, label))
        # Reserved example domains are suitable fixtures; personal addresses are not.
        for address in re.findall(r"[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", data):
            domain = address.rsplit("@", 1)[1].lower()
            if domain not in {"example.com", "example.org", "example.net"} and not domain.endswith((".test", ".invalid", ".example")):
                errors.append((item, "non-example email address"))
                break
    if errors:
        print("PUBLIC RELEASE CHECK: FAIL")
        for name, explanation in errors:
            print(f" - {name}: {explanation}")
        return 1
    print(f"PUBLIC RELEASE CHECK: GREEN — {len(list(filter(None, files)))} tracked files scanned (heuristic only)")
    return 0


if __name__ == "__main__":
    sys.exit(check_release())
