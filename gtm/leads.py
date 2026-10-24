"""Optional same-origin research-interest WSGI sidecar. Never sends messages."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

from .analytics import approved_content_id

INTERESTS = frozenset(("development", "creative-work", "everyday-work", "research"))
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+\Z")
SOURCE_CHANNELS = frozenset(("x", "threads", "reddit", "linkedin", "direct", "other"))


def _instant(value):
    value = dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(dt.timezone.utc)


def aggregate_leads(path, start_at, end_at, campaign=None, *, retention_days=30, as_of=None):
    """Read-only worker interface: no secret, database creation, chmod or PII.

    Selects [start_at, end_at) in UTC, limited to retained records. Stored
    timestamps have one-second resolution. Call with the real store's retention
    policy; this helper does not execute deletion or schedule retention work.
    """
    start, end = _instant(start_at), _instant(end_at)
    observed = _instant(as_of) if as_of is not None else dt.datetime.now(dt.timezone.utc)
    if start > end or type(retention_days) is not int or not 1 <= retention_days <= 90:
        raise ValueError("invalid period or retention policy")
    if campaign is not None and (not isinstance(campaign, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", campaign)):
        raise ValueError("invalid campaign")
    database = Path(path).absolute()
    if database.is_symlink() or not database.is_file():
        raise ValueError("existing private lead database required")
    retention_floor = (observed - dt.timedelta(days=retention_days)).timestamp()
    where, args = "created_at >= ? AND created_at < ?", [max(start.timestamp(), retention_floor), end.timestamp()]
    if campaign is not None:
        where += " AND campaign = ?"
        args.append(campaign)
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")  # count and groups must describe the same snapshot
        count = db.execute("SELECT COUNT(*) FROM leads WHERE " + where, args).fetchone()[0]
        groups = db.execute("SELECT channel, COUNT(*) FROM leads WHERE " + where + " GROUP BY channel", args).fetchall()
    finally:
        db.close()
    by_channel = {}
    for channel, n in groups:
        channel = channel if channel in SOURCE_CHANNELS else None
        by_channel[channel] = by_channel.get(channel, 0) + n
    return {"source": "lead_store", "mode": "observed", "campaign": campaign,
            "period": {"start_at": start.isoformat(), "end_at": end.isoformat(), "timezone": "UTC", "end_exclusive": True, "timestamp_resolution_seconds": 1},
            "observed_at": observed.isoformat(), "retention_days": retention_days,
            "stored_leads": count, "by_channel": [{"channel": channel, "stored_leads": n} for channel, n in by_channel.items()],
            "qualified_leads": None, "qualification_reason": "not_assessed", "verified_contacts": None,
            "scope": "retained_deduplicated_research_opt_ins; no email verification; no outreach"}


def normalize_email(value):
    if not isinstance(value, str):
        raise ValueError("invalid_email")
    value = value.strip().lower()
    if len(value) > 254 or not EMAIL.fullmatch(value) or len(value.split("@")[0]) > 64:
        raise ValueError("invalid_email")
    return value


class LeadStore:
    """Local PII store; do not put this database in worker artifacts/backups/logs.

    Hashing is for deduplication, not anonymization. Contact email remains PII.
    SQLite must live on a protected encrypted volume for production.
    """
    def __init__(self, path, *, secret, retention_days=30, clock=time.time):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("a persistent secret of at least 32 bytes is required")
        if not isinstance(retention_days, int) or not 1 <= retention_days <= 90:
            raise ValueError("retention_days must be between 1 and 90")
        self.path, self.secret, self.retention_days, self.clock = Path(path), secret, retention_days, clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("database must not be a symlink")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS leads (
                    email_key TEXT PRIMARY KEY, email TEXT NOT NULL,
                    interest TEXT NOT NULL, created_at INTEGER NOT NULL,
                    consent_version TEXT NOT NULL, analytics_consent INTEGER NOT NULL,
                    campaign TEXT, channel TEXT, content_id TEXT
                );
                CREATE INDEX IF NOT EXISTS leads_created_at ON leads(created_at);
                CREATE TABLE IF NOT EXISTS rate_limits (
                    client_key TEXT NOT NULL, window INTEGER NOT NULL,
                    attempts INTEGER NOT NULL, PRIMARY KEY(client_key, window)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _key(self, kind, value):
        return hmac.new(self.secret, (kind + ":" + value).encode(), hashlib.sha256).hexdigest()

    def allow_attempt(self, address, *, limit=5, window_seconds=600):
        """Shared across WSGI workers; raw IP is neither stored nor logged."""
        window = int(self.clock()) // window_seconds
        key = self._key("rate:" + str(window), address)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM rate_limits WHERE window < ?", (window - 1,))
            db.execute("INSERT INTO rate_limits VALUES (?, ?, 1) ON CONFLICT(client_key, window) DO UPDATE SET attempts=attempts+1", (key, window))
            return db.execute("SELECT attempts FROM rate_limits WHERE client_key=? AND window=?", (key, window)).fetchone()[0] <= limit

    def add(self, email, interest, *, consent, consent_version, analytics_consent=False, attribution=None):
        if consent is not True or not isinstance(consent_version, str) or not consent_version.strip():
            raise ValueError("research_consent_required")
        email = normalize_email(email)
        if interest not in INTERESTS:
            raise ValueError("invalid_interest")
        if type(analytics_consent) is not bool:
            raise ValueError("invalid_analytics_consent")
        attribution = (attribution or {}) if analytics_consent else {}
        now = int(self.clock())
        with self.connect() as db:
            db.execute("DELETE FROM leads WHERE created_at < ?", (now - self.retention_days * 86400,))
            cursor = db.execute("INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(email_key) DO NOTHING", (
                self._key("email", email), email, interest, now, consent_version,
                int(analytics_consent), attribution.get("campaign"), attribution.get("channel"), attribution.get("content_id")))
            return cursor.rowcount == 1

    def aggregate(self, start_date, end_date, campaign=None):
        """Local-only aggregate. Inclusive UTC dates, never exposes contact data.

        These are unverified, deduplicated opt-ins, not qualified/verified leads.
        Consentless source attribution is unavailable and kept separate.
        """
        start = dt.date.fromisoformat(start_date)
        end = dt.date.fromisoformat(end_date)
        if start > end:
            raise ValueError("invalid_period")
        start_ts = int(dt.datetime.combine(start, dt.time(), dt.timezone.utc).timestamp())
        end_ts = int(dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), dt.timezone.utc).timestamp())
        where, args = "created_at >= ? AND created_at < ?", [max(start_ts, int(self.clock()) - self.retention_days * 86400), end_ts]
        if campaign is not None:
            where += " AND campaign = ?"
            args.append(campaign)
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM leads WHERE " + where, args).fetchone()[0]
            groups = db.execute("SELECT channel, COUNT(*) FROM leads WHERE " + where + " GROUP BY channel", args).fetchall()
        return {"source": "lead_store", "mode": "observed", "period": {"start_date": start_date, "end_date": end_date, "timezone": "UTC"},
                "campaign": campaign, "stored_leads": count,
                "by_channel": [{"channel": channel, "stored_leads": n} for channel, n in groups],
                "qualified_leads": None, "qualification_reason": "not_assessed", "verified_contacts": None,
                "scope": "retained_deduplicated_research_opt_ins; no email verification; no outreach"}

    def prune(self):
        """Run at least daily, including periods without incoming submissions."""
        with self.connect() as db:
            cursor = db.execute("DELETE FROM leads WHERE created_at < ?", (int(self.clock()) - self.retention_days * 86400,))
            db.execute("DELETE FROM rate_limits WHERE window < ?", (int(self.clock()) // 600 - 1,))
            return cursor.rowcount

    def delete_by_email(self, email):
        """Operator-only privacy request action; no public enumeration endpoint."""
        with self.connect() as db:
            return db.execute("DELETE FROM leads WHERE email_key=?", (self._key("email", normalize_email(email)),)).rowcount


class LeadApp:
    """Explicitly configured WSGI application; no global app/default public bind.

    Serve at the website origin. REMOTE_ADDR must be the actual TCP client or a
    single trusted proxy's address; forwarded headers are deliberately ignored.
    Apply per-client limits in that proxy when all requests share its address.
    """
    def __init__(self, store, *, origin, privacy_contact, consent_version,
                 content_registry=None, web_root=None, allow_local_http=False):
        parsed = urlsplit(origin)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.scheme not in ("http", "https"):
            raise ValueError("origin must be an exact origin with no path")
        if parsed.scheme != "https" and not (allow_local_http and local):
            raise ValueError("HTTPS origin required")
        self.store, self.origin = store, origin
        self.privacy_contact = normalize_email(privacy_contact)
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", consent_version):
            raise ValueError("invalid consent version")
        self.consent_version, self.allow_local_http = consent_version, allow_local_http and local
        self.content_registry = content_registry or {}
        for key, value in self.content_registry.items():
            if not approved_content_id(key):
                raise ValueError("content ids must be approved experiment identifiers or c_ slugs")
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", value.get("campaign", "")):
                raise ValueError("invalid campaign slug")
            if value.get("channel") not in SOURCE_CHANNELS:
                raise ValueError("invalid source channel")
        self.web_root = Path(web_root) if web_root else Path(__file__).resolve().parents[1] / "web"

    def __call__(self, environ, start_response):
        headers = [("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                   ("Referrer-Policy", "no-referrer"), ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")]

        def respond(code, payload, content_type="application/json; charset=utf-8"):
            body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
            phrases = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 413: "Content Too Large", 415: "Unsupported Media Type", 429: "Too Many Requests", 503: "Service Unavailable"}
            start_response(str(code) + " " + phrases[code], headers + [("Content-Type", content_type), ("Content-Length", str(len(body)))])
            return [body]

        path, method = environ.get("PATH_INFO"), environ.get("REQUEST_METHOD")
        if self.allow_local_http:
            try:
                if not ipaddress.ip_address(environ.get("REMOTE_ADDR", "")).is_loopback:
                    return respond(403, {"ok": False, "error": "local_preview_only"})
            except ValueError:
                return respond(403, {"ok": False, "error": "local_preview_only"})
        if environ.get("wsgi.url_scheme") != "https" and not self.allow_local_http:
            return respond(403, {"ok": False, "error": "https_required"})
        if environ.get("HTTP_HOST") != urlsplit(self.origin).netloc:
            return respond(403, {"ok": False, "error": "untrusted_host"})
        if path in ("/research-interest", "/tracking.js") and method == "GET":
            filename = "research-interest.html" if path == "/research-interest" else "tracking.js"
            try:
                body = (self.web_root / filename).read_text()
            except OSError:
                return respond(503, {"ok": False, "error": "form_unavailable"})
            body = body.replace("{{PRIVACY_CONTACT}}", html.escape(self.privacy_contact)).replace("{{RETENTION_DAYS}}", str(self.store.retention_days)).replace("{{NOTICE_VERSION}}", self.consent_version).replace("{{CONTENT_IDS}}", html.escape(json.dumps(list(self.content_registry)), quote=True))
            return respond(200, body.encode(), "text/html; charset=utf-8" if path == "/research-interest" else "text/javascript; charset=utf-8")
        if path != "/api/research-interest":
            return respond(404, {"ok": False, "error": "not_found"})
        if method != "POST":
            return respond(405, {"ok": False, "error": "post_required"})
        if environ.get("HTTP_ORIGIN") != self.origin:
            return respond(403, {"ok": False, "error": "untrusted_origin"})
        if environ.get("CONTENT_TYPE", "").split(";")[0].lower().strip() != "application/json":
            return respond(415, {"ok": False, "error": "json_required"})
        try:
            length = int(environ.get("CONTENT_LENGTH", ""))
            if not 0 < length <= 4096:
                return respond(413, {"ok": False, "error": "invalid_body_size"})
            address = str(ipaddress.ip_address(environ.get("REMOTE_ADDR", "")))
        except ValueError:
            return respond(400, {"ok": False, "error": "invalid_request"})
        try:
            if not self.store.allow_attempt(address):
                headers.append(("Retry-After", "600"))
                return respond(429, {"ok": False, "error": "rate_limited"})
            raw = environ["wsgi.input"].read(length)
            if len(raw) != length:
                return respond(400, {"ok": False, "error": "invalid_request"})
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) - {"email", "interest", "consent", "consent_version", "analytics_consent", "content_id", "website"}:
                raise ValueError("invalid_fields")
            if data.get("website"):
                return respond(200, {"ok": True, "new": False})
            if data.get("consent_version") != self.consent_version:
                raise ValueError("notice_changed_reload_form")
            content_id = data.get("content_id")
            if content_id is not None and not isinstance(content_id, str):
                raise ValueError("invalid_content_id")
            attribution = dict(self.content_registry.get(content_id, {}))
            if attribution:
                attribution["content_id"] = content_id
            new = self.store.add(data.get("email"), data.get("interest"), consent=data.get("consent"),
                consent_version=self.consent_version, analytics_consent=data.get("analytics_consent", False), attribution=attribution)
            return respond(200, {"ok": True, "new": new})
        except (ValueError, TypeError, UnicodeError):
            return respond(400, {"ok": False, "error": "invalid_fields_or_consent"})
        except (sqlite3.Error, OSError):
            return respond(503, {"ok": False, "error": "temporarily_unavailable"})
