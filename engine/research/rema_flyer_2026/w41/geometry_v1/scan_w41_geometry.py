#!/usr/bin/env python3
"""Read-only W41 holdout: reuse W42 V2 PLAQUE GEOMETRY, no catalog identity.

Input: 29 offline Tesseract TSVs and 29 local images.
This does not call link_prior_prices_by_regions(), because W41 Tjek offers
have not been supplied.  Never treat these results as verified products/prices.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict, Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

PRIOR_PATTERN = re.compile(r"(?<!\w)F[ØO]R\s+(\d{1,3}[.,]\d{2})(?!\d)", re.I)
FUSED_PRICE = re.compile(r"[A-Za-zÆØÅæøå]{3,15}\s+(\d{3,4})")
EXPECTED_TSV_COLUMNS = {
    "level", "block_num", "par_num", "line_num", "word_num",
    "left", "top", "width", "height", "text",
}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for piece in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(piece)
    return h.hexdigest()


def tsv_lines(path: Path, image_width: int, image_height: int) -> list[dict]:
    """Group OCR words on a Tesseract line, converting TOP-left to BOTTOM-left.

    Every line retains its source TSV coordinates; text/geometry are not
    modified to improve apparent recall.
    """
    groups: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as inp:
        reader = csv.DictReader(inp, delimiter="\t")
        fields = set(reader.fieldnames or [])
        if not EXPECTED_TSV_COLUMNS <= fields:
            raise ValueError(f"Malformed TSV {path}: missing {sorted(EXPECTED_TSV_COLUMNS-fields)}")
        for row in reader:
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                x = int(row["left"]); top = int(row["top"])
                width = int(row["width"]); height = int(row["height"])
                key = tuple(int(row[c]) for c in ("block_num", "par_num", "line_num"))
                word_no = int(row["word_num"])
            except (ValueError, TypeError, KeyError) as ex:
                raise ValueError(f"Invalid TSV geometry {path}: {ex}") from ex
            if not (0 <= x < image_width and 0 <= top < image_height and
                    width > 0 and height > 0 and x+width <= image_width+2 and
                    top+height <= image_height+2):
                # Reject out-of-bounds entries, never clamp into a purported hit.
                continue
            groups[key].append({"text":text,"word_no":word_no,
                                "x0":x,"x1":min(x+width,image_width),
                                "top":top,"bottom":min(top+height,image_height)})
    result = []
    for key, words in groups.items():
        words.sort(key=lambda w:(w["word_no"],w["x0"]))
        x0 = min(w["x0"] for w in words); x1 = max(w["x1"] for w in words)
        top = min(w["top"] for w in words); bottom = max(w["bottom"] for w in words)
        result.append({
            "text":" ".join(w["text"] for w in words),
            "box":{
                "x":round(x0/image_width,8),
                "y":round(1-bottom/image_height,8),
                "width":round((x1-x0)/image_width,8),
                "height":round((bottom-top)/image_height,8),
            },
            "tsv_source_line":list(key),
            "tsv_box_pixels":{"left":x0,"top":top,"width":x1-x0,"height":bottom-top},
        })
    # Put lines top to bottom, left to right; stable line indices.
    result.sort(key=lambda ln:(-ln["box"]["y"]-ln["box"]["height"],ln["box"]["x"]))
    return result


def priors_on_page(page_number: int, lines: list[dict]) -> list[dict]:
    result = []
    for i, line in enumerate(lines):
        m = PRIOR_PATTERN.search(line["text"])
        if not m:
            continue
        amount = Decimal(m.group(1).replace(",","."))
        result.append({
            "signal_id":f"W41-{page_number:02d}-prior-{i}","page":page_number,
            "line_index":i,"kind":"prior_price","context":line["text"],
            "box":line["box"], "ocr_prior_nok":str(amount),
        })
    return result


def price_vocabulary(page: dict, ad_layout) -> set[Decimal]:
    """OCR-only possible current amounts, NEVER substitute a catalog value."""
    prices = {r["amount"] for r in ad_layout.current_price_observations(page, None)}
    for line in page["lines"]:
        m=FUSED_PRICE.fullmatch(line["text"].strip())
        if m:
            prices.add(Decimal(int(m.group(1)))/100)
    return prices


def scan_page(page: dict, image_path: Path, ad_layout) -> dict:
    priors=priors_on_page(page["page"],page["lines"])
    badges=ad_layout._blue_price_badge_boxes(image_path)
    observed_price_vocabulary=price_vocabulary(page,ad_layout)
    observations=[]
    for prior in priors:
        badge=ad_layout._badge_for_prior(prior,badges,priors)
        o={
            "page":page["page"],"line_index":prior["line_index"],
            "prior_text":prior["context"],"ocr_prior_nok":prior["ocr_prior_nok"],
            "prior_box":prior["box"],"blue_plaque_box":badge,
            "printed_current_candidates":[],
            "verified_product_identity":False,"verified_previous_shelf_price":False,
            "database_writes":False,
        }
        if badge is None:
            o["status"]="unresolved_no_unique_aligned_blue_plaque"
        else:
            current=ad_layout._badge_printed_price_hits(page,badge,observed_price_vocabulary)
            valid={}
            for hit in current:
                amount=hit["amount"]
                if amount >= Decimal(prior["ocr_prior_nok"]):
                    continue
                k=(str(amount),tuple(hit["line_indices"]))
                valid[k] = {
                    "ocr_current_nok":str(amount),
                    "line_indices":hit["line_indices"],
                    "ocr_source":hit["source"],
                }
            o["printed_current_candidates"]=list(valid.values())
            amounts={v["ocr_current_nok"] for v in valid.values()}
            if not amounts:
                o["status"]="aligned_plaque_current_price_unreadable_review_only"
            elif len(amounts)>1:
                o["status"]="ambiguous_current_price_on_plaque_review_only"
            else:
                o["status"]="plaque_with_ocr_current_price_review_only"
        observations.append(o)
    return {"page":page["page"],"blue_plaque_count":len(badges),
            "ocr_prior_count":len(priors),"observations":observations}


def private_dest(path: Path) -> bool:
    parts=path.resolve().parts
    return any(parts[i:i+3]==("data","private","tjek") for i in range(len(parts)-2))


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src",required=True,type=Path,help="Existing v2 parser source folder")
    ap.add_argument("--tsv-dir",required=True,type=Path)
    ap.add_argument("--image-dir",required=True,type=Path)
    ap.add_argument("--out-dir",required=True,type=Path)
    args=ap.parse_args(argv)
    dest=args.out_dir
    if not private_dest(dest) or dest.exists() or dest.is_symlink():
        ap.error("Output must be a NEW directory inside data/private/tjek")
    src=args.src.resolve()
    if not (src/"ad_layout_regions.py").is_file():
        ap.error("V2 ad_layout_regions.py missing")
    if not args.tsv_dir.is_dir() or not args.image_dir.is_dir():
        ap.error("W41 TSV/image directory missing")
    sys.path.insert(0,str(src))
    import cv2
    import ad_layout_regions as al
    if not hasattr(al,"_blue_price_badge_boxes") or not hasattr(al,"_badge_printed_price_hits"):
        ap.error("Source does not contain W42 V2 plaque recovery; stop")
    pages=[];results=[];files=[]
    for number in range(1,30):
        name=f"page-{number:02d}"
        tsv=args.tsv_dir/f"{name}.tsv"
        image=args.image_dir/f"{name}.jpg"
        if not (tsv.is_file() and image.is_file() and tsv.stat().st_size>0):
            ap.error(f"Missing input for page {number:02d}: {tsv} / {image}")
        image_arr=cv2.imread(str(image))
        if image_arr is None:
            ap.error(f"Unreadable image: {image}")
        height,width=image_arr.shape[:2]
        lines=tsv_lines(tsv,width,height)
        page={"page":number,"status":"processed","lines":lines}
        pages.append(page)
        results.append(scan_page(page,image,al))
        files.extend(({"path":str(tsv),"sha256":digest(tsv)},
                      {"path":str(image),"sha256":digest(image)}))
    document={"format":"nutrivalue_ocr_lines_v1", "week":"2026-W41",
              "catalog_id":"priser24-rema-1000-W41-2026-tsv-local-review",
              "page_count":29,"pages":pages,
              "source":"Tesseract 5.5.3 TSV local (per-user), not Vision OCR"}
    observations=[o for r in results for o in r["observations"]]
    counts=Counter(o["status"] for o in observations)
    report={
        "schema":"nutrivalue_w41_independent_plaque_geometry_v1",
        "week":"2026-W41","page_count":29,
        "parser_version":"existing V2 blue-plaque methods (no source changes)",
        "source_parser_sha256":digest(src/"ad_layout_regions.py"),
        "tjek_crosscheck":False,"all_offers_extracted":False,
        "historical_price_verified":False,"database_writes":False,
        "ocr_prior_count":len(observations),
        "blue_plaque_count":sum(r["blue_plaque_count"] for r in results),
        "counts":dict(sorted(counts.items())),
        "pages":results,
        "limitations":[
            "Tests V2 plaque geometry and OCR evidence only, NOT V2 offer-matching recall or precision.",
            "W41 catalog Tjek offers not provided; using W42 offers would leak wrong identity.",
            "English-only Tesseract and 700x1040 images can miss printed values.",
            "W41 page 1 Kjeldsberg advert repeats an observed W42 promotion.",
            "All proposals are review-only; no verified old shelf prices or DB changes.",
        ],
    }
    dest.mkdir(parents=True)
    def save(name,obj):
        (dest/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    save("ocr-lines-w41.json",document)
    save("geometry-review-w41.json",report)
    save("input-manifest-w41.json",{
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "parser_sha256":report["source_parser_sha256"],
        "files":files,
    })
    readme=["# REMA 1000 W41 — independent V2 plaque geometry (review-only)","",
           "Pages: 29 / 29", f"OCR FØR with explicit monetary amount: {len(observations)}",
           f"Detected blue price plaques: {report['blue_plaque_count']}","",
           "## Status counts"]
    readme += [f"- {k}: {v}" for k,v in sorted(counts.items())]
    readme += ["","## Per-page prior observations"]
    for r in results:
        for o in r["observations"]:
            printed=", ".join(c["ocr_current_nok"] for c in o["printed_current_candidates"]) or "?"
            readme.append(f"- p{r['page']:02d}, OCR line {o['line_index']}: {o['prior_text']} -> {printed} — {o['status']}")
    readme += ["","## Restrictions"]+[f"- {v}" for v in report["limitations"]]
    (dest/"audit.md").write_text("\n".join(readme)+"\n",encoding="utf-8")
    print(f"W41 geometry-only: 29 pages, {len(observations)} explicit FØR OCR markers, {report['blue_plaque_count']} blue plaques")
    for k,v in sorted(counts.items()):print(f"  {k}: {v}")
    print("NO HTTP | NO OCR/LLM CALLS | NO TJEK IDENTITY | NO DB WRITES")
    print(f"Report: {dest/'audit.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
