"""TASK-024: move one week's working state between private GCS and a workspace.

Bucket layout (uniform access, public access prevented):

  state/offers.sqlite3                       the offer database (one object)
  config/<week>/extra-api-investigation/oslo_stores.json
                                             saved public Tjek shop listing (Extra geo filter)
  archive/weekly/<week>/<run>/...            collector files except page images (kept)
  archive/promotions/<week>/<run>/...        answers, cross-checks, OCR, budget (kept)
  archive/reports/<week>/<run>.md            weekly report (kept)
  page-images/<week>/<run>/pages/...         working page images (lifecycle: delete at 14 days)
  reviews/<date>/<time>-<id>.json            review decisions (TASK-058; create-only, kept)

The database is never mounted: it is downloaded, changed locally and uploaded
with a generation precondition, so two runs can never silently overwrite each
other. Archive objects are written with ``if_generation_match=0`` (create
only): history is never replaced.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

DB_OBJECT = "state/offers.sqlite3"
CONFIG_PREFIX = "config/"           # mirrored into data/private/tjek/weekly/
REVIEWS_PREFIX = "reviews/"         # mirrored into data/private/reviews/


class SyncError(RuntimeError):
    """State could not be moved safely."""


class GcsStore:
    """Thin google-cloud-storage adapter (the package exists only in the cloud image)."""

    def __init__(self, bucket_name: str, client=None):
        if client is None:
            from google.cloud import storage   # optional cloud dependency
            client = storage.Client()
        self.bucket = client.bucket(bucket_name)

    def generation(self, name: str) -> int:
        blob = self.bucket.get_blob(name)
        return int(blob.generation) if blob is not None else 0

    def names(self, prefix: str) -> list[str]:
        return sorted(blob.name for blob in self.bucket.list_blobs(prefix=prefix))

    def download(self, name: str, path: Path, generation: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        kwargs = {} if generation is None else {"if_generation_match": generation}
        self.bucket.blob(name).download_to_filename(str(path), **kwargs)

    def upload(self, path: Path, name: str, if_generation_match: int) -> None:
        self.bucket.blob(name).upload_from_filename(
            str(path), if_generation_match=if_generation_match)

    def read_bytes(self, name: str, generation: int | None = None) -> bytes:
        kwargs = {} if generation is None else {"if_generation_match": generation}
        return self.bucket.blob(name).download_as_bytes(**kwargs)

    def create(self, name: str, data: bytes, content_type: str) -> None:
        """Write a new object; fails if the name exists (never replaces)."""
        self.bucket.blob(name).upload_from_string(data, content_type=content_type,
                                                  if_generation_match=0)


def _private(root: Path) -> Path:
    return Path(root) / "data" / "private"


def pull(store, root: Path, week: str, *, allow_empty_db: bool = False) -> dict:
    """Download the database, config and this week's earlier runs. Returns generations."""
    root = Path(root)
    generation = store.generation(DB_OBJECT)
    if generation:
        store.download(DB_OBJECT, _private(root) / "offers.sqlite3", generation)
    elif not allow_empty_db:
        raise SyncError("state/offers.sqlite3 is missing; upload the database before the "
                        "first run (or start a new history explicitly)")
    for name in store.names(CONFIG_PREFIX):
        rel = name[len(CONFIG_PREFIX):]
        if ".." in Path(rel).parts:
            raise SyncError("unsafe config object name")
        store.download(name, _private(root) / "tjek" / "weekly" / rel)
    reviews = 0
    for name in store.names(REVIEWS_PREFIX):
        rel = name[len(REVIEWS_PREFIX):]
        if ".." in Path(rel).parts:
            raise SyncError("unsafe review object name")
        store.download(name, _private(root) / "reviews" / rel)
        reviews += 1
    count = 0
    for kind in ("weekly", "promotions"):
        prefix = f"archive/{kind}/{week}/"
        for name in store.names(prefix):
            rel = name[len(prefix):]
            if ".." in Path(rel).parts:
                raise SyncError("unsafe archive object name")
            store.download(name, _private(root) / "tjek" / kind / week / rel)
            count += 1
    return {"db_generation": generation, "archive_files": count, "review_files": reviews}


def _upload_tree(store, local: Path, prefix: str) -> int:
    count = 0
    for path in sorted(p for p in local.rglob("*") if p.is_file()):
        rel = path.relative_to(local).as_posix()
        store.upload(path, f"{prefix}{rel}", if_generation_match=0)
        count += 1
    return count


def push(store, root: Path, week: str, run_name: str, db_generation: int) -> dict:
    """Upload the new run (create-only) and then the database (generation-checked)."""
    private = _private(Path(root))
    run_dir = private / "tjek" / "weekly" / week / run_name
    if not (run_dir / "manifest.json").exists():
        raise SyncError("the run to upload has no manifest")
    uploaded = 0
    pages = run_dir / "pages"
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(run_dir).as_posix()
        if pages in path.parents:
            name = f"page-images/{week}/{run_name}/{rel}"
        else:
            name = f"archive/weekly/{week}/{run_name}/{rel}"
        store.upload(path, name, if_generation_match=0)
        uploaded += 1
    promotions = private / "tjek" / "promotions" / week / run_name
    if promotions.exists():
        uploaded += _upload_tree(store, promotions, f"archive/promotions/{week}/{run_name}/")
    report = private / "tjek" / "reports" / week / f"{run_name}.md"
    if report.exists():
        store.upload(report, f"archive/reports/{week}/{run_name}.md", if_generation_match=0)
        uploaded += 1
    db = private / "offers.sqlite3"
    store.upload(db, DB_OBJECT, if_generation_match=db_generation)
    return {"uploaded_files": uploaded,
            "db_sha256": hashlib.sha256(db.read_bytes()).hexdigest()}
