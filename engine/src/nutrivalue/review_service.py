"""TASK-058: the promotion review page as a Cloud Run service (WSGI, stdlib core).

Same page and API as the local TASK-055 review (``promotion_review_web``), but:

* sign-in with Google (Identity Services button); only one verified e-mail
  address gets a session, kept in an HMAC-signed, HttpOnly, Secure cookie;
* data come from the private bucket: the service downloads the database
  snapshot, replays every decision object from ``reviews/`` into its own
  working copy and serves from that copy;
* a decision is written as a new create-only object (never the database), so
  it survives any weekly run; the weekly job replays it into the database;
* page images are read from ``page-images/`` and checked against the SHA-256
  stored with the page; after the 14-day lifecycle they are simply gone.

Run with gunicorn: ``nutrivalue.review_service:create_from_env()``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import threading
import time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from nutrivalue.cloud_sync import DB_OBJECT, REVIEWS_PREFIX
from nutrivalue.promotion_extraction import ExtractionError, _connect
from nutrivalue.promotion_review_web import HEADER, MAX_BODY, PAGE, WEEK, week_overview
from nutrivalue.review_log import ReviewLogError, encode, make, object_name, replay, \
    target_exists, validate

COOKIE = "nv_review"
SESSION_SECONDS = 8 * 3600
REFRESH_SECONDS = 20
BASE_HEADERS = [("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"),
                ("Strict-Transport-Security", "max-age=31536000")]
APP_CSP = ("default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; "
           "script-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; "
           "form-action 'self'")
LOGIN_CSP = ("default-src 'self'; script-src 'unsafe-inline' https://accounts.google.com/gsi/client; "
             "frame-src https://accounts.google.com/gsi/; "
             "connect-src 'self' https://accounts.google.com/gsi/; "
             "style-src 'unsafe-inline' https://accounts.google.com/gsi/style; "
             "img-src 'self' https://*.googleusercontent.com; frame-ancestors 'none'; "
             "base-uri 'none'")


class ServiceError(RuntimeError):
    """The service is not configured safely."""


class Auth:
    """Google sign-in for exactly one verified address, then a signed cookie."""

    def __init__(self, client_id: str, secret: str, allowed_email: str, *, verify=None,
                 clock=time.time):
        if not client_id or len(secret or "") < 32 or "@" not in (allowed_email or ""):
            raise ServiceError("Google client ID, a session secret of at least 32 characters "
                               "and the allowed e-mail address are required")
        self.client_id, self.allowed = client_id, allowed_email.strip().casefold()
        self.key = hashlib.sha256(secret.encode()).digest()
        self.verify, self.clock = verify or _google_verify, clock

    def _sign(self, payload: bytes) -> str:
        return base64.urlsafe_b64encode(hmac.new(self.key, payload, hashlib.sha256)
                                        .digest()).decode().rstrip("=")

    def issue(self, email: str) -> str:
        payload = json.dumps({"e": email, "x": int(self.clock()) + SESSION_SECONDS},
                             separators=(",", ":")).encode()
        token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        return (f"{COOKIE}={token}.{self._sign(payload)}; Path=/; HttpOnly; Secure; "
                f"SameSite=Lax; Max-Age={SESSION_SECONDS}")

    def email(self, environ) -> str | None:
        """The signed-in allowed address, or None."""
        value = _cookie(environ.get("HTTP_COOKIE", ""), COOKIE)
        if value is None or value.count(".") != 1:
            return None
        token, signature = value.split(".")
        try:
            payload = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            data = json.loads(payload)
        except (ValueError, TypeError):
            return None
        if not hmac.compare_digest(self._sign(payload), signature):
            return None
        if not isinstance(data, dict) or data.get("e") != self.allowed:
            return None
        if not isinstance(data.get("x"), int) or data["x"] < self.clock():
            return None
        return self.allowed

    def login(self, credential: str) -> str:
        """Verify a Google ID token; returns the address or raises PermissionError."""
        try:
            claims = self.verify(credential, self.client_id)
        except ValueError:
            raise PermissionError("Google sign-in could not be verified") from None
        email = str(claims.get("email", "")).casefold()
        if claims.get("email_verified") is not True or email != self.allowed:
            raise PermissionError("this Google account has no access")
        return email


def _cookie(header: str, name: str) -> str | None:
    """One cookie value. Parsed by hand: http.cookies stops at the first cookie it
    considers illegal, and Google's own ``g_state={"i_l":0}`` is such a cookie."""
    for part in header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value
    return None


def _google_verify(credential: str, client_id: str) -> dict:
    from google.auth.transport.requests import Request   # cloud image only
    from google.oauth2 import id_token
    return id_token.verify_oauth2_token(credential, Request(), client_id)


class BucketData:
    """The bucket database plus every decision object, as one local working copy."""

    def __init__(self, store, schema_dir: Path, workdir: Path, *, clock=time.monotonic,
                 refresh_seconds: float = REFRESH_SECONDS):
        self.store, self.schema_dir, self.workdir = store, Path(schema_dir), Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.clock, self.refresh_seconds = clock, refresh_seconds
        self.lock = threading.RLock()
        self.generation, self.applied, self.checked = None, set(), None
        self.working = self.workdir / "working.sqlite3"
        self.pages = {}

    def db(self) -> Path:
        with self.lock:
            now = self.clock()
            if self.checked is not None and now - self.checked < self.refresh_seconds:
                return self.working
            generation = self.store.generation(DB_OBJECT)
            if not generation:
                raise ExtractionError("the bucket has no offer database yet")
            if generation != self.generation:
                fresh = self.workdir / "snapshot.sqlite3"
                self.store.download(DB_OBJECT, fresh, generation)
                shutil.copyfile(fresh, self.working)
                fresh.unlink()
                self.generation, self.applied = generation, set()
            new = [n for n in self.store.names(REVIEWS_PREFIX) if n not in self.applied]
            if new:
                decisions = []
                for name in new:
                    try:
                        decisions.append(json.loads(self.store.read_bytes(name)))
                    except ValueError:
                        decisions.append(None)
                replay(self.working, self.schema_dir, decisions)
                self.applied.update(new)
            self.checked = now
            return self.working

    def add(self, decision: dict) -> None:
        """Save a new decision in the bucket (create-only), then in the working copy."""
        with self.lock:
            name = object_name(decision)
            self.store.create(name, encode(decision), "application/json")
            replay(self.db(), self.schema_dir, [decision])
            self.applied.add(name)

    def page_image(self, run_id: str, catalog_id: str, page: int, sha256: str) -> bytes | None:
        with self.lock:
            files = self.pages.get(run_id)
            if files is None:
                files = {}
                prefix = f"archive/weekly/{run_id}/"
                for name in self.store.names(prefix):
                    if name.endswith(".pages.json") and "/" not in name[len(prefix):]:
                        for entry in json.loads(self.store.read_bytes(name))["pages"]:
                            files[(entry["catalog_id"], entry["page"])] = entry["file"]
                if files:
                    self.pages[run_id] = files
        rel = files.get((catalog_id, page))
        if rel is None or ".." in Path(rel).parts:
            return None
        name = f"page-images/{run_id}/{rel}"
        if not self.store.generation(name):
            return None
        data = self.store.read_bytes(name)
        return data if hashlib.sha256(data).hexdigest() == sha256 else None


def _page_ref(db_path: Path, schema_dir: Path, extraction_id: str, catalog_id: str,
              page: int):
    with _connect(db_path, schema_dir) as db:
        return db.execute("""SELECT e.run_id, p.image_sha256 FROM promo_pages p
                             JOIN promo_extractions e USING(extraction_id)
                             WHERE p.extraction_id=? AND p.catalog_id=? AND p.page=?""",
                          (extraction_id, catalog_id, page)).fetchone()


def create_app(data: BucketData, auth: Auth, schema_dir: Path):
    """The WSGI application."""
    schema_dir = Path(schema_dir)

    def respond(start, status, body: bytes, kind: str, extra=(), csp=APP_CSP):
        headers = [("Content-Type", kind), ("Content-Length", str(len(body))),
                   ("Content-Security-Policy", csp), *BASE_HEADERS, *extra]
        start(f"{status.value} {status.phrase}", headers)
        return [body]

    def as_json(start, status, payload, extra=()):
        return respond(start, status, json.dumps(payload, ensure_ascii=False).encode(),
                       "application/json", extra)

    def read_json(environ):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BODY:
            raise ValueError("bad body")
        return json.loads(environ["wsgi.input"].read(length))

    def app(environ, start):
        method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "/")
        if method == "HEAD":          # same headers as GET (gunicorn drops the body)
            method = "GET"
        query = parse_qs(environ.get("QUERY_STRING", ""))
        try:
            if path == "/health" and method == "GET":
                return as_json(start, HTTPStatus.OK, {"status": "healthy",
                                                      "service": "nutrivalue-review"})
            if path == "/login" and method == "GET":
                body = LOGIN_PAGE.replace("__CLIENT_ID__", json.dumps(auth.client_id)[1:-1])
                return respond(start, HTTPStatus.OK, body.encode(), "text/html; charset=utf-8",
                               [("Cross-Origin-Opener-Policy", "same-origin-allow-popups")],
                               LOGIN_CSP)
            if path == "/auth/google" and method == "POST":
                if environ.get(f"HTTP_{HEADER.upper().replace('-', '_')}") != "1":
                    return as_json(start, HTTPStatus.FORBIDDEN, {"error": "missing header"})
                try:
                    email = auth.login(str(read_json(environ).get("credential") or ""))
                except PermissionError as exc:
                    return as_json(start, HTTPStatus.FORBIDDEN, {"error": str(exc)})
                return as_json(start, HTTPStatus.OK, {"ok": True},
                               [("Set-Cookie", auth.issue(email))])
            email = auth.email(environ)
            if email is None:
                if path.startswith("/api/") or path == "/page-image" or method != "GET":
                    return as_json(start, HTTPStatus.UNAUTHORIZED,
                                   {"error": "sign in with the allowed Google account"})
                start("302 Found", [("Location", "/login"), *BASE_HEADERS])
                return [b""]
            if path == "/logout" and method == "POST":
                return as_json(start, HTTPStatus.OK, {"ok": True}, [(
                    "Set-Cookie", f"{COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0")])
            if path == "/" and method == "GET":
                return respond(start, HTTPStatus.OK, PAGE.replace(
                    "<h1>NutriValue — promotion review</h1>", HOSTED_HEADER).encode(),
                    "text/html; charset=utf-8")
            if path == "/api/week" and method == "GET":
                week = (query.get("week") or [None])[0]
                if week is not None and not WEEK.fullmatch(week):
                    return as_json(start, HTTPStatus.BAD_REQUEST, {"error": "bad week"})
                return as_json(start, HTTPStatus.OK, week_overview(data.db(), schema_dir, week))
            if path == "/page-image" and method == "GET":
                ref = _page_ref(data.db(), schema_dir, (query.get("extraction") or [""])[0],
                                (query.get("catalog") or [""])[0],
                                int((query.get("page") or ["0"])[0]))
                image = None if ref is None else data.page_image(
                    ref["run_id"], (query.get("catalog") or [""])[0],
                    int((query.get("page") or ["0"])[0]), ref["image_sha256"])
                if image is None:
                    return as_json(start, HTTPStatus.NOT_FOUND, {
                        "error": "page image is no longer available (kept 14 days)"})
                return respond(start, HTTPStatus.OK, image, "image/jpeg")
            if path == "/api/decision" and method == "POST":
                if environ.get(f"HTTP_{HEADER.upper().replace('-', '_')}") != "1":
                    return as_json(start, HTTPStatus.FORBIDDEN, {"error": "missing review header"})
                body = read_json(environ)
                item = {"extraction_id": str(body["extraction_id"]),
                        "catalog_id": str(body["catalog_id"]), "page": int(body["page"]),
                        "item_no": None if body.get("item_no") is None else int(body["item_no"])}
                decision = make(item, str(body["decision"]), str(body.get("note") or ""), email)
                with _connect(data.db(), schema_dir) as db:
                    if not target_exists(db, decision):
                        return as_json(start, HTTPStatus.BAD_REQUEST,
                                       {"error": "unknown page or item"})
                data.add(validate(decision))
                return as_json(start, HTTPStatus.OK, {"ok": True})
            return as_json(start, HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (KeyError, TypeError, ValueError, ReviewLogError, ExtractionError) as exc:
            return as_json(start, HTTPStatus.BAD_REQUEST, {"error": str(exc)[:200]})
        except sqlite3.Error:
            return as_json(start, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "database error"})

    return app


def create_from_env(environ=None, *, store=None, verify=None):
    """gunicorn entry point: ``nutrivalue.review_service:create_from_env()``."""
    env = os.environ if environ is None else environ
    bucket = env.get("NV_BUCKET", "")
    if not bucket:
        raise ServiceError("NV_BUCKET is required")
    auth = Auth(env.get("GOOGLE_OAUTH_CLIENT_ID", ""), env.get("REVIEW_SESSION_SECRET", ""),
                env.get("REVIEW_ALLOWED_EMAIL", ""), verify=verify)
    if store is None:
        from nutrivalue.cloud_sync import GcsStore
        store = GcsStore(bucket)
    root = Path(__file__).resolve().parents[2]
    data = BucketData(store, root / "analytics", Path(env.get("NV_WORKSPACE",
                                                              "/tmp/nutrivalue-review")))
    return create_app(data, auth, root / "analytics")


HOSTED_HEADER = ("<h1>NutriValue — promotion review</h1><p class=muted style=\"margin-top:-.5rem\">"
                 "<button class=link onclick=\"fetch('/logout',{method:'POST'})"
                 ".then(()=>location.href='/login')\">Sign out</button></p>")

LOGIN_PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>NutriValue review — sign in</title>
<style>:root{color:#172033;background:#f6f8fb;font:16px/1.5 system-ui,sans-serif}body{margin:0}
.card{max-width:420px;margin:12vh auto;background:#fff;border:1px solid #d6dde8;border-radius:12px;padding:2rem}
.muted{color:#536176}.error{color:#a01625}</style>
<div class=card><h1>NutriValue review</h1><p class=muted>Sign in with the Google account that has access.</p>
<div id=g_id_onload data-client_id="__CLIENT_ID__" data-callback=signedIn data-auto_prompt=false></div>
<div class=g_id_signin data-type=standard data-size=large></div><p id=error class=error></p></div>
<script>
async function signedIn(r){const e=document.getElementById('error');e.textContent='';
 try{const x=await fetch('/auth/google',{method:'POST',headers:{'Content-Type':'application/json','X-NutriValue-Review':'1'},body:JSON.stringify({credential:r.credential})});
 if(x.ok){location.href='/';return}const b=await x.json().catch(()=>({}));e.textContent=b.error||('Sign-in failed ('+x.status+')')}
 catch(_){e.textContent='Sign-in failed.'}}
</script><script src="https://accounts.google.com/gsi/client" async></script></html>
"""
