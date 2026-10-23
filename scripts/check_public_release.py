#!/usr/bin/env python3
"""Conservative offline guard against accidental private artifacts in public release.

It is a safety net, not a proof that arbitrary source code contains no secrets.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
errors = []
for item in filter(None, files):
    path = Path(item)
    low = item.lower()
    parts = [p.lower() for p in path.parts]
    if any(name in parts for name in (".venv", ".secrets", "__pycache__", "private", "raw", "downloads")):
        errors.append((item, "forbidden data or local environment folder"))
    if low.endswith((".sqlite", ".sqlite3", ".db", ".pem", ".key", ".p12", ".pfx", ".jpg", ".jpeg", ".png", ".webp", ".pdf")):
        errors.append((item, "binary/provider/image or key file needs independent publication review"))
    if path.name == ".env" or path.name.startswith(".env.") or "credentials" in path.name.lower():
        errors.append((item, "environment/credential path"))
    if path.suffix.lower() not in (".py", ".sql", ".md", ".json", ".yml", ".yaml", ".sh", ".txt", ".toml") and path.name not in (
        ".gitignore", "LICENSE", "Dockerfile", "Makefile"
    ):
        continue
    if not (ROOT / path).is_file():
        continue
    # The checker stores pattern fragments, not real credentials.
    data = (ROOT / path).read_text(encoding="utf-8", errors="replace")
    checks = (
        ("private key marker", r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        ("GitHub token", "gh" + r"p_[A-Za-z0-9]{35,}"),
        ("GitHub fine-grained token", "github" + r"_pat_[A-Za-z0-9_]{40,}"),
        ("Google API key", "AI" + r"za[0-9A-Za-z_-]{35}"),
        ("AWS access key", "AK" + r"IA[0-9A-Z]{16}"),
        ("absolute Mac home", "/" + r"Users/[A-Za-z0-9_.-]+/"),
    )
    for label, pattern in checks:
        if re.search(pattern, data):
            errors.append((item, label))
if errors:
    print("PUBLIC RELEASE CHECK: FAIL")
    for name, explanation in errors:
        print(f" - {name}: {explanation}")
    sys.exit(1)
print(f"PUBLIC RELEASE CHECK: GREEN — {len(list(filter(None, files)))} tracked files scanned (heuristic only)")
