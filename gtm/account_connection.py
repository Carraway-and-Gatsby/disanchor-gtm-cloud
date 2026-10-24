"""Read-only Upload-Post inventory and destination identity checks.

No OAuth, upload, rename, disconnect, or provider-account mutation is exposed.
Only normalized allowlisted fields leave this module; missing identity is never
inferred from display names, profile count limits, or plan names.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from .publisher import UploadPostPublisher, PublisherError


PLATFORMS = frozenset({"x", "threads", "tiktok", "instagram", "facebook", "youtube", "linkedin",
                       "pinterest", "reddit", "bluesky", "google_business", "snapchat", "discord", "telegram"})
SUPPORTED = frozenset({"x", "threads"})
MAX_PROFILE_DETAILS = 32
_PROFILE = re.compile(r"[A-Za-z0-9_@-]{1,200}\Z")
_NATIVE_ID = re.compile(r"[A-Za-z0-9_.:@-]{1,200}\Z")
_BINDING_ID = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
_SECRET = re.compile(r"(?:\b(?:sk-|clnp_)[A-Za-z0-9_-]{8,}|api[_-]?key\s*[:=]|bearer\s+|access[_-]?token\s*[:=])", re.I)
_SAFE_ERRORS = frozenset({"account_response_invalid", "profile_identity_invalid", "duplicate_profile_identity",
                          "binding_invalid", "binding_profile_mismatch", "binding_unsupported_platform",
                          "binding_connection_missing", "binding_reauthentication_required", "binding_identity_unverified",
                          "binding_authentication_unverified", "binding_account_mismatch", "duplicate_binding_identity",
                          "profile_detail_limit_exceeded"})


def _count(value):
    return value if type(value) is int and 0 <= value < 2**53 else None


def _validate_binding(binding):
    required = {"id", "upload_post_profile", "platform", "native_account_id"}
    if (not isinstance(binding, dict) or set(binding) != required
        or not isinstance(binding["id"], str) or not _BINDING_ID.fullmatch(binding["id"])
        or not isinstance(binding["upload_post_profile"], str) or not _PROFILE.fullmatch(binding["upload_post_profile"])
        or not isinstance(binding["platform"], str) or binding["platform"] not in SUPPORTED
        or not isinstance(binding["native_account_id"], str) or not _NATIVE_ID.fullmatch(binding["native_account_id"])):
        raise ValueError("binding_invalid")


class UploadPostAccountClient:
    simulated = False

    def __init__(self, api_key, transport=None):
        self._publisher = UploadPostPublisher(api_key, user="connection-check", transport=transport)
        self._secret = api_key
        self.network_calls = 0

    def _get(self, path):
        self.network_calls += 1
        payload = self._publisher._call("GET", path)
        if payload.get("success") is not True:
            raise ValueError("account_response_invalid")
        return payload

    def _text(self, value, *, maximum=100, pattern=None):
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or self._secret in value or _SECRET.search(value):
            return None
        if pattern and not pattern.fullmatch(value):
            return None
        return value

    def _native_id(self, value):
        # Some provider schema versions encode numeric account IDs as integers.
        # Preserve every digit as text, including IDs larger than JS safe integers.
        if type(value) is int and 0 < value < 10**200:
            value = str(value)
        return self._text(value, maximum=200, pattern=_NATIVE_ID)

    def _profile(self, profile):
        if not isinstance(profile, dict):
            raise ValueError("account_response_invalid")
        name = self._text(profile.get("username"), maximum=200, pattern=_PROFILE)
        accounts = profile.get("social_accounts")
        if not name:
            raise ValueError("profile_identity_invalid")
        if not isinstance(accounts, dict):
            raise ValueError("account_response_invalid")
        rows = []
        for platform in sorted(SUPPORTED | (set(accounts) & PLATFORMS)):
            value = accounts.get(platform)
            row = {"profile_username": name, "platform": platform, "native_account_id": None,
                   "handle": None, "display_name": None, "connection_state": "missing",
                   "supported": platform in SUPPORTED}
            if value is None or value == "":
                rows.append(row)
                continue
            if not isinstance(value, dict):
                row["connection_state"] = "identity_unverified"
                rows.append(row)
                continue
            row["native_account_id"] = self._native_id(value.get("username"))
            handle = value.get("handle")
            if isinstance(handle, str):
                handle = handle.removeprefix("@")
            row["handle"] = self._text(handle, pattern=re.compile(r"[A-Za-z0-9_.-]{1,100}\Z"))
            row["display_name"] = self._text(value.get("display_name"))
            reauth = value.get("reauth_required")
            if reauth is True:
                row["connection_state"] = "reauth_required"
            elif not row["native_account_id"]:
                row["connection_state"] = "identity_unverified"
            elif reauth is not False:
                row["connection_state"] = "authentication_unverified"
            else:
                row["connection_state"] = "connected"
            rows.append(row)
        return rows

    def profiles(self):
        body = self._get("/uploadposts/users")
        profiles = body.get("profiles")
        if not isinstance(profiles, list) or len(profiles) > 1000:
            raise ValueError("account_response_invalid")
        parsed, seen = [], set()
        for profile in profiles:
            normalized = self._profile(profile)
            identity = normalized[0]["profile_username"]
            if identity in seen:
                raise ValueError("duplicate_profile_identity")
            seen.add(identity)
            # The actual list response can omit native username while the
            # documented single-profile GET supplies it. Never fall back to handle.
            hydrate = any(platform in PLATFORMS and isinstance(account, dict) and account.get("username") is None
                          for platform, account in profile["social_accounts"].items())
            parsed.append((identity, normalized, hydrate))
        if sum(hydrate for _, _, hydrate in parsed) > MAX_PROFILE_DETAILS:
            raise ValueError("profile_detail_limit_exceeded")
        rows = []
        for identity, normalized, hydrate in parsed:
            if hydrate:
                detail = self._get("/uploadposts/users/" + quote(identity, safe=""))
                normalized = self._profile(detail.get("profile"))
                if normalized[0]["profile_username"] != identity:
                    raise ValueError("binding_profile_mismatch")
            rows.extend(normalized)
        return rows

    def current_user(self):
        body = self._get("/uploadposts/me")
        usage = body.get("api_usage")
        usage = usage if isinstance(usage, dict) else {}
        used, limit = _count(usage.get("count")), _count(usage.get("limit"))
        remaining = max(0, limit - used) if used is not None and limit is not None else None
        plan = body.get("plan")
        plan = plan.lower() if isinstance(plan, str) else None
        plan = plan if plan in {"free", "default", "basic", "professional", "advanced", "business", "enterprise"} else None
        return {"authenticated": True, "plan": plan, "scope": "api_account", "used": used, "limit": limit,
                "remaining": remaining, "quota_verified": remaining is not None,
                "missing_reasons": {key: "provider_not_returned" for key, value in
                                    (("used", used), ("limit", limit), ("remaining", remaining)) if value is None}}

    def verify_binding(self, profile_username, platform, native_account_id):
        _validate_binding({"id": "verify", "upload_post_profile": profile_username,
                           "platform": platform, "native_account_id": native_account_id})
        body = self._get("/uploadposts/users/" + quote(profile_username, safe=""))
        rows = self._profile(body.get("profile"))
        if rows[0]["profile_username"] != profile_username:
            raise ValueError("binding_profile_mismatch")
        row = next(item for item in rows if item["platform"] == platform)
        failure = {"missing": "binding_connection_missing", "reauth_required": "binding_reauthentication_required",
                   "identity_unverified": "binding_identity_unverified", "authentication_unverified": "binding_authentication_unverified"}.get(row["connection_state"])
        if failure:
            raise ValueError(failure)
        if row["native_account_id"] != native_account_id:
            raise ValueError("binding_account_mismatch")
        return row


def check_connection(api_key=None, bindings=None, transport=None):
    """Inspect authenticated inventory, then verify explicitly supplied bindings.

    All calls are GET. The caller supplies the credential; no implicit env read.
    This result is connection evidence and never publishing authorization.
    """
    result = {"status": "disabled", "reason": "credential_missing", "inventory": [], "quota": None,
              "bindings": [], "posts_sent": 0, "network_calls": 0}
    if not api_key:
        return result
    client = None
    try:
        if bindings is not None and (not isinstance(bindings, list) or len(bindings) > 100):
            raise ValueError("binding_invalid")
        seen_ids, seen_accounts = set(), set()
        for binding in bindings or []:
            _validate_binding(binding)
            if isinstance(api_key, str) and any(api_key in value or _SECRET.search(value) for value in binding.values()):
                raise ValueError("binding_invalid")
            account = (binding["platform"], binding["native_account_id"])
            if binding["id"] in seen_ids or account in seen_accounts:
                raise ValueError("duplicate_binding_identity")
            seen_ids.add(binding["id"])
            seen_accounts.add(account)
        client = UploadPostAccountClient(api_key, transport)
        result["inventory"] = client.profiles()
        result["quota"] = client.current_user()
        result["status"], result["reason"] = "needs_binding", "explicit_account_binding_required"
        for binding in bindings or []:
            row = client.verify_binding(binding["upload_post_profile"], binding["platform"], binding["native_account_id"])
            result["bindings"].append({"id": binding["id"], "status": "verified_read_only", **row})
        if bindings:
            result["status"], result["reason"] = "verified_read_only", None
    except PublisherError as exc:
        codes = {401: "authentication_failed", 403: "permission_or_plan_denied", 404: "profile_not_found", 429: "rate_limited"}
        result["status"], result["reason"] = "blocked", codes.get(exc.http_status, "provider_unavailable")
    except (ValueError, TypeError, KeyError) as exc:
        code = str(exc)
        result["status"], result["reason"] = "blocked", code if code in _SAFE_ERRORS else "configuration_or_response_invalid"
    result["network_calls"] = client.network_calls if client else 0
    return result
