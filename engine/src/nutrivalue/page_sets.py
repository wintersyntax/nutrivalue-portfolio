"""TASK-056: self-contained, hash-verified sets of catalogue page images.

A page set lets the promotion benchmark run on any pages: a TASK-051 run
(all pages, only hotspot-free, or only pages with offer hotspots), a folder
of saved page images, or an official PDF rendered page by page. Images are
copied/rendered into the set directory so the set stays reproducible after
weekly working images are moved away. No network.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
from pathlib import Path

SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{2,90}")


class PageSetError(ValueError):
    """A page set cannot be built or read without guessing."""


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(manifest_path: Path) -> dict:
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text())
    base = manifest_path.parent
    seen = set()
    for page in data["pages"]:
        key = (page["catalog_id"], page["page"])
        if key in seen:
            raise PageSetError(f"duplicate page {key}")
        seen.add(key)
        page["path"] = base / page["file"]
    return data


def _write(manifest_path: Path, data: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    clean = {**data, "pages": [{k: v for k, v in p.items() if k != "path"}
                               for p in data["pages"]]}
    manifest_path.write_text(json.dumps(clean, ensure_ascii=False, indent=1))


def _open(manifest_path: Path, name: str) -> dict:
    if Path(manifest_path).exists():
        return load(manifest_path)
    return {"schema_version": 1, "name": name, "pages": []}


def _add(data: dict, manifest_path: Path, *, source: Path | bytes, catalog_id: str,
         page: int, meta: dict) -> None:
    if not SAFE_ID.fullmatch(catalog_id):
        raise PageSetError("unsafe catalog id")
    if any((p["catalog_id"], p["page"]) == (catalog_id, page) for p in data["pages"]):
        raise PageSetError(f"page {catalog_id} p{page} already in set")
    rel = f"pages/{catalog_id}/p{page:03d}.jpg"
    target = Path(manifest_path).parent / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, bytes):
        target.write_bytes(source)
    else:
        shutil.copyfile(source, target)
    data["pages"].append({"catalog_id": catalog_id, "page": page, "file": rel,
                          "sha256": _sha(target), **meta})


def run_pages(run_dir: Path, include: str = "all") -> list[dict]:
    """Page dicts (with path and catalogue context) of a TASK-051 run, in place.

    include = all | hotspot_free | with_hotspots. Nothing is copied.
    """
    if include not in ("all", "hotspot_free", "with_hotspots"):
        raise PageSetError("include must be all, hotspot_free or with_hotspots")
    run_dir = Path(run_dir)
    pages = []
    for pages_file in sorted(run_dir.glob("*.pages.json")):
        dealer = pages_file.name.split(".")[0]
        spots_file = run_dir / f"{dealer}.hotspots.json"
        if not spots_file.is_file():
            raise PageSetError(f"{spots_file.name} is required to know hotspot pages")
        spots = json.loads(spots_file.read_text())
        envelope = json.loads((run_dir / f"{dealer}.json").read_text())
        catalogs = {e["catalog"]["id"]: e["catalog"] for e in envelope["entries"]}
        spotted = {c["catalog_id"]: {n for found in c["hotspot_offer_pages"].values() for n in found}
                   for c in spots["catalogs"]}
        for page in json.loads(pages_file.read_text())["pages"]:
            has = page["page"] in spotted.get(page["catalog_id"], set())
            if include == "hotspot_free" and has or include == "with_hotspots" and not has:
                continue
            cat = catalogs[page["catalog_id"]]
            pages.append({
                "catalog_id": page["catalog_id"], "page": page["page"], "file": page["file"],
                "sha256": page["sha256"], "path": run_dir / page["file"], "dealer_id": dealer,
                "chain": cat["dealer"]["name"] if isinstance(cat.get("dealer"), dict) else dealer,
                "catalog_label": cat.get("label") or "", "valid_from": cat["run_from"],
                "valid_to": cat["run_till"], "has_hotspots": has,
                "source": f"tjek run {run_dir.parent.name}/{run_dir.name}"})
    return sorted(pages, key=lambda p: (p["catalog_id"], p["page"]))


def add_from_run(manifest_path: Path, run_dir: Path, *, include: str = "all",
                 sample: int | None = None, seed: int = 0, name: str = "") -> dict:
    """Copy pages of a TASK-051 run into the set (optionally a seeded sample)."""
    candidates = run_pages(run_dir, include)
    data = _open(manifest_path, name or Path(run_dir).name)
    if sample is not None:
        candidates = sorted(random.Random(seed).sample(candidates, min(sample, len(candidates))),
                            key=lambda c: (c["catalog_id"], c["page"]))
    for page in candidates:
        if _sha(page["path"]) != page["sha256"]:
            raise PageSetError(f"run image hash mismatch: {page['file']}")
        meta = {k: page[k] for k in ("dealer_id", "chain", "catalog_label", "valid_from",
                                     "valid_to", "has_hotspots", "source")}
        _add(data, manifest_path, source=page["path"], catalog_id=page["catalog_id"],
             page=page["page"], meta=meta)
    _write(manifest_path, data)
    return data


def add_from_images(manifest_path: Path, images: list[Path], *, catalog_id: str, chain: str,
                    valid_from: str, valid_to: str, catalog_label: str = "",
                    has_hotspots: dict[int, bool] | None = None, source: str,
                    name: str = "") -> dict:
    """Ordered page images (page 1..N) of one catalogue."""
    data = _open(manifest_path, name or catalog_id)
    for number, image in enumerate(images, 1):
        _add(data, manifest_path, source=Path(image), catalog_id=catalog_id, page=number,
             meta={"dealer_id": None, "chain": chain, "catalog_label": catalog_label,
                   "valid_from": valid_from, "valid_to": valid_to,
                   "has_hotspots": (has_hotspots or {}).get(number), "source": source})
    _write(manifest_path, data)
    return data


def add_from_pdf(manifest_path: Path, pdf: Path, *, catalog_id: str, chain: str,
                 valid_from: str, valid_to: str, catalog_label: str = "",
                 width_px: int = 1400, name: str = "") -> dict:
    """Render each PDF page to a JPEG of width_px (needs optional PyMuPDF)."""
    try:
        import pymupdf
    except ImportError as exc:
        raise PageSetError("PDF rendering needs the optional pymupdf package") from exc
    data = _open(manifest_path, name or catalog_id)
    doc = pymupdf.open(str(pdf))
    for index, page in enumerate(doc, 1):
        zoom = width_px / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        _add(data, manifest_path, source=pix.tobytes("jpeg", jpg_quality=88),
             catalog_id=catalog_id, page=index,
             meta={"dealer_id": None, "chain": chain, "catalog_label": catalog_label,
                   "valid_from": valid_from, "valid_to": valid_to, "has_hotspots": None,
                   "source": f"official PDF {Path(pdf).name} sha256 {_sha(pdf)}"})
    _write(manifest_path, data)
    return data
