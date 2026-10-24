"""Small, fail-closed Upload-Post text publisher; no implicit retries.

Transport contract: callable(method, url, headers, body_bytes_or_none) ->
{"status_code": int, "headers": dict, "body": dict}. Tests inject this callable.
The default transport only contacts https://api.upload-post.com, with a 30s
timeout, a bounded response, and redirects disabled. See docs/upload-post.md.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib import error, parse, request
from .safety import posting_locked


class PublisherError(Exception):
    """Safe to log: never contains a provider body or credentials."""

    def __init__(self, code: str, *, http_status: int | None = None,
                 retry_after: int | None = None):
        self.code = code
        self.http_status = http_status
        self.retry_after = retry_after
        super().__init__(code)


class ValidationError(PublisherError):
    pass


class AuthenticationError(PublisherError):
    pass


class PlanRestrictionError(PublisherError):
    pass


class RateLimitError(PublisherError):
    pass


class NotFoundError(PublisherError):
    pass


class ProviderUnavailableError(PublisherError):
    pass


class ProtocolError(PublisherError):
    pass


class AmbiguousSubmission(PublisherError):
    """The write may have reached the provider. Reconcile, never blind retry."""


class AmbiguousCancellation(PublisherError):
    """Cancellation may have happened; reconcile before reporting it complete."""


METRIC_NAMES = ("impressions", "views", "reach", "likes", "comments", "shares",
                "saves", "clicks", "link_clicks", "followers", "replies", "reposts", "quotes",
                "leads", "booked_calls", "applications")
TEXT_PLATFORMS = frozenset(("x", "threads"))
_LINK = re.compile(r"(?:[a-z]+://|www\.|\b(?:[\w-]+\.)+[a-z]{2,63}\b|"
                   r"\b(?:\d{1,3}\.){3}\d{1,3}/|\[\.\]|hxxps?:|[。．｡])", re.I)
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:@-]{1,200}\Z")


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value) and value >= 0 else None


def _metrics(values=None, reason="provider_not_returned"):
    values = values if isinstance(values, dict) else {}
    metrics = {key: _number(values.get(key)) for key in METRIC_NAMES}
    missing = {key: ("requires_first_party_attribution" if key in
                    {"leads", "booked_calls", "applications"} else reason)
               for key, value in metrics.items() if value is None}
    # A social API cannot establish funnel conversions; never ingest them here.
    for key in ("leads", "booked_calls", "applications"):
        metrics[key] = None
        missing[key] = "requires_first_party_attribution"
    return metrics, missing


def _identifier(value):
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value)
        if 0 < len(text) <= 500 and not any(ord(c) < 32 for c in text):
            return text
    return None


def _url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        parsed = parse.urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username:
        return value
    return None


def _outcome(row, *, job_id=None, platform=None, default="unknown", simulated=False):
    """Only allowlisted fields leave the provider adapter."""
    state = str(row.get("status", "")).lower()
    if row.get("skipped") is True or state == "skipped":
        status = "skipped"
    elif row.get("fallback_to_inbox") is True:
        status = "draft"
    elif state in {"pending", "queued", "processing", "in_progress", "retryable"}:
        status = "pending"
    elif row.get("success") is False or state == "failed":
        status = "failed"
    elif state in {"completed", "published", "success"} or row.get("success") is True:
        status = "published"
    elif state in {"cancelled", "canceled"}:
        status = "canceled"
    else:
        status = default
    metrics, missing = _metrics(row.get("post_metrics", row.get("metrics")))
    post_id = next((_identifier(row.get(key)) for key in
                    ("platform_post_id", "post_id", "tweet_id", "id")
                    if _identifier(row.get(key)) is not None), None)
    url = _url(row.get("post_url", row.get("url")))
    fields_missing = {}
    if url is None:
        fields_missing["published_url"] = "not_published" if status != "published" else "provider_not_returned"
    if post_id is None:
        fields_missing["platform_post_id"] = "provider_not_returned"
    return {"status": status, "provider_job_id": _identifier(row.get("job_id")) or job_id,
            "provider_request_id": _identifier(row.get("request_id")),
            "external_id": _identifier(row.get("external_id")),
            "platform": platform or row.get("platform"), "published_url": url,
            "platform_post_id": post_id, "metrics": metrics,
            "metric_missing_reasons": missing, "missing_reasons": fields_missing,
            "simulated": simulated}


def _validate(platform, text, scheduled_at, idempotency_key, *, enforce_future=True):
    if platform not in TEXT_PLATFORMS:
        raise ValidationError("unsupported_text_platform")
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValidationError("invalid_text")
    if platform == "threads" and len(text.encode("utf-16-le")) // 2 > 500:
        raise ValidationError("text_too_long")
    if platform == "x":
        if _LINK.search(text):
            raise ValidationError("x_links_not_enabled_provider_would_strip")
        # Deliberately conservative without implementing X's full Unicode weighting.
        if sum(1 if ord(c) <= 0x7F else 2 for c in text) > 280:
            raise ValidationError("x_text_too_long_for_single_post")
    if not isinstance(idempotency_key, str) or not _SAFE_ID.fullmatch(idempotency_key):
        raise ValidationError("invalid_idempotency_key")
    try:
        when = scheduled_at if isinstance(scheduled_at, datetime) else \
            datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError
        when = when.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        raise ValidationError("scheduled_at_requires_iso8601_timezone") from None
    if enforce_future:
        now = datetime.now(timezone.utc)
        if not now < when <= now + timedelta(days=365):
            raise ValidationError("schedule_must_be_future_within_365_days")
    return when.isoformat().replace("+00:00", "Z")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_transport(method, url, headers, body):
    if method != "GET" and posting_locked():
        raise ValidationError("cloud_posting_locked")
    req = request.Request(url, data=body, headers=headers, method=method)
    opener = request.build_opener(_NoRedirect())
    try:
        response = opener.open(req, timeout=30)
    except error.HTTPError as exc:
        response = exc
    with response:
        status = response.status
        headers = dict(response.headers.items())
        # Error bodies are never read: no accidental body/token logging.
        if not 200 <= status < 300:
            return {"status_code": status, "headers": headers, "body": {}}
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ProtocolError("provider_response_too_large")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ProtocolError("invalid_provider_json") from None
    return {"status_code": status, "headers": headers, "body": payload}


class UploadPostPublisher:
    """Official API adapter. Caller owns approval, quota policy and durable state."""

    BASE = "https://api.upload-post.com/api"
    simulated = False

    def __init__(self, api_key: str, user: str, transport: Callable | None = None):
        if not isinstance(api_key, str) or not api_key.strip() or any(c in api_key for c in "\r\n"):
            raise ValidationError("invalid_api_key_configuration")
        if not isinstance(user, str) or not user.strip() or any(ord(c) < 32 for c in user):
            raise ValidationError("invalid_profile_configuration")
        self._api_key = api_key
        self.user = user
        self._transport = transport or _http_transport

    def _call(self, method, path, *, query=None, form=None, headers=None):
        # Reject before building headers or invoking even an injected transport.
        # GET observations remain available; no write or cancellation can escape.
        if method != "GET" and posting_locked():
            raise ValidationError("cloud_posting_locked")
        url = self.BASE + path
        if query:
            url += "?" + parse.urlencode(query)
        request_headers = {"Authorization": "Apikey " + self._api_key, "Accept": "application/json"}
        request_headers.update(headers or {})
        body = None
        if form is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = parse.urlencode(form).encode("utf-8")
        try:
            result = self._transport(method, url, request_headers, body)
        except Exception:
            # Even an injected transport exception can contain a token or full request.
            if method == "POST":
                raise AmbiguousSubmission("submission_outcome_unknown_reconcile") from None
            if method == "DELETE":
                raise AmbiguousCancellation("cancellation_outcome_unknown_reconcile") from None
            raise ProviderUnavailableError("provider_transport_failed") from None
        if not isinstance(result, dict) or not isinstance(result.get("status_code"), int):
            self._malformed(method)
        status = result["status_code"]
        if not 200 <= status < 300:
            exception = {400: ValidationError, 401: AuthenticationError,
                         403: PlanRestrictionError, 404: NotFoundError,
                         429: RateLimitError}.get(status, ProviderUnavailableError)
            code = {400: "provider_rejected_input", 401: "provider_authentication_failed",
                    403: "provider_permission_or_plan_denied", 404: "provider_not_found",
                    429: "provider_rate_or_quota_limit"}.get(status, "provider_http_error")
            if method == "POST" and (status >= 500 or status in {408, 409}):
                exception, code = AmbiguousSubmission, "submission_outcome_unknown_reconcile"
            if method == "DELETE" and status >= 500:
                exception, code = AmbiguousCancellation, "cancellation_outcome_unknown_reconcile"
            retry_after = None
            response_headers = result.get("headers", {})
            if isinstance(response_headers, dict):
                retry = next((v for k, v in response_headers.items() if str(k).lower() == "retry-after"), None)
                if isinstance(retry, (int, str)) and str(retry).isdigit():
                    retry_after = int(retry)
            raise exception(code, http_status=status, retry_after=retry_after)
        payload = result.get("body")
        if not isinstance(payload, dict):
            self._malformed(method)
        return payload

    @staticmethod
    def _malformed(method):
        if method == "POST":
            raise AmbiguousSubmission("submission_response_unusable_reconcile")
        if method == "DELETE":
            raise AmbiguousCancellation("cancellation_response_unusable_reconcile")
        raise ProtocolError("provider_response_schema_invalid")

    def schedule(self, platform, text, scheduled_at, idempotency_key):
        when = _validate(platform, text, scheduled_at, idempotency_key)
        data = self._call("POST", "/upload_text", headers={"Idempotency-Key": idempotency_key},
                          form={"user": self.user, "platform[]": platform, "title": text,
                                "scheduled_date": when, "async_upload": "true",
                                "external_id": idempotency_key, "request_id": idempotency_key})
        if data.get("success") is False:
            raise ValidationError("provider_rejected_submission")
        job_id = _identifier(data.get("job_id"))
        if job_id is None:
            raise AmbiguousSubmission("scheduled_job_id_missing_reconcile")
        result = _outcome({"job_id": job_id, "request_id": data.get("request_id"),
                           "external_id": idempotency_key}, platform=platform, default="scheduled")
        result["scheduled_at"] = when
        return result

    def status(self, job_id):
        self._validate_job_id(job_id)
        data = self._call("GET", "/uploadposts/status", query={"job_id": job_id})
        entries = data.get("results", [])
        if isinstance(entries, dict):
            entries = [dict(value, platform=key) for key, value in entries.items() if isinstance(value, dict)]
        if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
            raise ProtocolError("provider_status_schema_invalid")
        normalized = [_outcome(row, job_id=job_id,
                               default="published" if data.get("status") == "completed" else "pending")
                      for row in entries]
        if len(normalized) == 1:
            result = copy.deepcopy(normalized[0])
            # success=true on an actively queued result does not mean publication.
            if data.get("status") in {"pending", "queued", "processing", "in_progress"} \
                    and result["status"] == "published" and not result["published_url"] \
                    and not result["platform_post_id"]:
                result["status"] = "pending"
                normalized[0]["status"] = "pending"
        else:
            result = _outcome(data, job_id=job_id)
            states = {row["status"] for row in normalized}
            if states and states != {"published"} and result["status"] == "published":
                result["status"] = next(iter(states)) if len(states) == 1 else "partial"
        result["provider_request_id"] = _identifier(data.get("request_id")) or result["provider_request_id"]
        result["external_id"] = _identifier(data.get("external_id")) or result["external_id"]
        result["results"] = normalized
        return result

    def history(self):
        rows = []
        for page in range(1, 101):
            data = self._call("GET", "/uploadposts/history",
                              query={"profile_username": self.user, "page": page, "limit": 100})
            batch = data.get("history")
            if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                raise ProtocolError("provider_history_schema_invalid")
            for row in batch:
                # Defense in depth: do not accept another profile if the provider returns one.
                if row.get("profile_username", self.user) != self.user:
                    continue
                item = _outcome(row)
                item["published_at"] = row.get("upload_timestamp")
                rows.append(item)
            if len(batch) < 100 or (isinstance(data.get("total"), int) and page * 100 >= data["total"]):
                return rows
        raise ProtocolError("history_pagination_bound_reached")

    def analytics(self, post_ids=None):
        if post_ids is None:
            data = self._call("GET", "/analytics/" + parse.quote(self.user, safe=""),
                              query={"platforms": "threads,x"})
            platforms = {}
            for platform in ("threads", "x"):
                row = data.get(platform, {})
                row = row if isinstance(row, dict) else {}
                reason = "provider_platform_unavailable" if row.get("error") or row.get("message") else "provider_not_returned"
                metrics, missing = _metrics(row, reason)
                platforms[platform] = {"metrics": metrics, "metric_missing_reasons": missing}
            return {"scope": "profile", "platforms": platforms, "simulated": False}
        if not isinstance(post_ids, (list, tuple)) or len(post_ids) > 50 or \
                any(not isinstance(item, str) for item in post_ids):
            raise ValidationError("post_ids_requires_at_most_50_request_ids")
        posts = []
        for request_id in dict.fromkeys(post_ids):
            self._validate_job_id(request_id)
            data = self._call("GET", "/uploadposts/post-analytics/" + parse.quote(request_id, safe=""))
            platforms = data.get("platforms")
            if not isinstance(platforms, dict):
                raise ProtocolError("provider_analytics_schema_invalid")
            for platform, row in platforms.items():
                if not isinstance(row, dict):
                    raise ProtocolError("provider_analytics_schema_invalid")
                item = _outcome(row, platform=platform)
                item["provider_request_id"] = request_id
                item["metrics_scope"] = "post"
                if row.get("post_metrics_error"):
                    item["metrics"], item["metric_missing_reasons"] = _metrics(reason="provider_post_metrics_unavailable")
                posts.append(item)
        return {"scope": "post", "posts": posts, "simulated": False}

    def quota(self):
        data = self._call("GET", "/uploadposts/me")
        usage = data.get("api_usage", data.get("usage", {}))
        usage = usage if isinstance(usage, dict) else {}
        used, limit = _number(usage.get("count")), _number(usage.get("limit"))
        remaining = max(0, limit - used) if used is not None and limit is not None else None
        missing = {key: "provider_not_returned" for key, value in
                   (("used", used), ("limit", limit), ("remaining", remaining)) if value is None}
        return {"plan": data.get("plan") if isinstance(data.get("plan"), str) else None,
                "used": used, "limit": limit, "remaining": remaining,
                "missing_reasons": missing, "simulated": False}

    def cancel(self, job_id):
        self._validate_job_id(job_id)
        data = self._call("DELETE", "/uploadposts/schedule/" + parse.quote(job_id, safe=""))
        if data.get("success") is not True:
            raise AmbiguousCancellation("cancellation_unconfirmed_reconcile")
        result = _outcome({"job_id": job_id, "status": "canceled"})
        result["credits_refunded"] = _number(data.get("credits_refunded"))
        return result

    @staticmethod
    def _validate_job_id(value):
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise ValidationError("invalid_provider_id")


class MockPublisher:
    """Deterministic in-memory simulation. Never sends HTTP or invents live URLs."""

    simulated = True

    def __init__(self, api_key=None, user="simulation", transport=None):
        self.user = user
        self._jobs = {}
        self._keys = {}

    def schedule(self, platform, text, scheduled_at, idempotency_key):
        when = _validate(platform, text, scheduled_at, idempotency_key, enforce_future=False)
        fingerprint = hashlib.sha256(json.dumps([self.user, platform, text, when], ensure_ascii=False).encode()).hexdigest()
        if idempotency_key in self._keys:
            old_fingerprint, job_id = self._keys[idempotency_key]
            if fingerprint != old_fingerprint:
                raise ValidationError("idempotency_key_payload_conflict")
            return copy.deepcopy(self._jobs[job_id])
        job_id = "mock_" + hashlib.sha256((str(self.user) + ":" + idempotency_key).encode()).hexdigest()[:20]
        item = _outcome({"job_id": job_id, "external_id": idempotency_key},
                        platform=platform, default="scheduled", simulated=True)
        item["scheduled_at"] = when
        item["metrics"], item["metric_missing_reasons"] = _metrics(reason="simulation_no_live_metrics")
        self._keys[idempotency_key] = fingerprint, job_id
        self._jobs[job_id] = item
        return copy.deepcopy(item)

    def status(self, job_id):
        if job_id not in self._jobs:
            raise NotFoundError("mock_job_not_found")
        return copy.deepcopy(self._jobs[job_id])

    def history(self):
        return copy.deepcopy(list(self._jobs.values()))

    def analytics(self, post_ids=None):
        if post_ids is not None:
            return {"scope": "post", "posts": [self.status(key) for key in post_ids], "simulated": True}
        metrics, missing = _metrics(reason="simulation_no_live_metrics")
        return {"scope": "profile", "platforms": {platform: {"metrics": copy.deepcopy(metrics),
                "metric_missing_reasons": copy.deepcopy(missing)} for platform in ("threads", "x")},
                "simulated": True}

    def quota(self):
        used = sum(item["status"] != "canceled" for item in self._jobs.values())
        return {"plan": "simulation", "used": used, "limit": 10, "remaining": max(0, 10-used),
                "missing_reasons": {}, "simulated": True}

    def cancel(self, job_id):
        self.status(job_id)
        self._jobs[job_id]["status"] = "canceled"
        return self.status(job_id)
