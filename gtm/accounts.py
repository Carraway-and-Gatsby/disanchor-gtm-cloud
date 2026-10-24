"""Local account strategy data; this module cannot authenticate, publish or execute."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib


FIELDS = frozenset({"id", "upload_post_profile", "platform", "native_account_id", "handle",
                    "role", "language", "approach", "voice", "prompt_file", "reviewed"})
PLATFORMS = frozenset({"instagram", "x", "threads", "linkedin"})
SUPPORTED = frozenset({"x", "threads"})
MAX_REGISTRY_BYTES = 60000
MAX_PROMPT_BYTES = 700
MAX_CONTEXT_BYTES = 540
_ID = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
_PROFILE = re.compile(r"[A-Za-z0-9_@-]{1,200}\Z")
_NATIVE = re.compile(r"[A-Za-z0-9_.:@-]{1,200}\Z")
_HANDLE = re.compile(r"@?[A-Za-z0-9_.-]{1,100}\Z")
_PROMPT_PATH = re.compile(r"prompts/accounts/[a-z][a-z0-9_-]{0,79}\.md\Z")
_SECRET = re.compile(r"(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:sk-|clnp_)[A-Za-z0-9_-]{8,}|bearer\s+|api[_-]?key\s*[:=])", re.I)


def _read_bounded(path: Path, limit: int, label: str) -> str:
    """Never follow a final symlink or read an unbounded/special input file."""
    try:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(label + "_not_regular")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError(label + "_not_regular")
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise ValueError(label + "_too_large")
        return raw.decode("utf-8")
    except (OSError, UnicodeError):
        raise ValueError(label + "_unavailable") from None


def _descriptive(value: str, maximum: int) -> bool:
    return (0 < len(value) <= maximum and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and not any(mark in value for mark in ("`", "$(", "<", ">", "\\"))
            and not _SECRET.search(value))


class AccountRegistry:
    """Load a fixed snapshot; expose copies and no credentials or action methods.

    Metadata and account style text remain contextual data for the global content
    policy. `reviewed` records local identity review, never a content approval or
    proof that a provider connection is still authenticated.
    """

    def __init__(self, path):
        self.path = Path(path).absolute()
        raw = _read_bounded(self.path, MAX_REGISTRY_BYTES, "account_registry")
        try:
            document = tomllib.loads(raw)
        except tomllib.TOMLDecodeError:
            raise ValueError("account_registry_invalid_toml") from None
        if (set(document) != {"accounts"} or not isinstance(document["accounts"], list)
                or not 1 <= len(document["accounts"]) <= 32):
            raise ValueError("account_registry_invalid_shape")
        base = self.path.parent
        prompt_root = base / "prompts" / "accounts"
        for directory in (base / "prompts", prompt_root):
            if directory.is_symlink():
                raise ValueError("account_prompt_root_symlink")
        self._records, self._prompts = {}, {}
        profile_platforms, native_identities = set(), set()
        for item in document["accounts"]:
            if (not isinstance(item, dict) or set(item) != FIELDS
                    or type(item["reviewed"]) is not bool
                    or any(type(item[key]) is not str for key in FIELDS - {"reviewed"})):
                raise ValueError("account_record_invalid_fields")
            row = dict(item)
            if (not _ID.fullmatch(row["id"]) or not _PROFILE.fullmatch(row["upload_post_profile"])
                    or any(_SECRET.search(row[key]) for key in FIELDS - {"reviewed"})
                    or row["platform"] not in PLATFORMS or row["role"] not in {"brand", "builder", "official"}
                    or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", row["language"])
                    or not _descriptive(row["approach"], 70) or not _descriptive(row["voice"], 50)
                    or (row["native_account_id"] and not _NATIVE.fullmatch(row["native_account_id"]))
                    or (row["handle"] and not _HANDLE.fullmatch(row["handle"]))):
                raise ValueError("account_record_invalid_metadata")
            key = row["id"]
            pair = (row["upload_post_profile"], row["platform"])
            if key in self._records or pair in profile_platforms:
                raise ValueError("duplicate_account_identity")
            native = (row["platform"], row["native_account_id"])
            if row["native_account_id"] and native in native_identities:
                raise ValueError("duplicate_native_account_identity")
            relative = PurePosixPath(row["prompt_file"])
            if (relative.is_absolute() or ".." in relative.parts
                    or not _PROMPT_PATH.fullmatch(row["prompt_file"])):
                raise ValueError("account_prompt_path_invalid")
            prompt_path = base / relative
            if prompt_path.is_symlink() or prompt_path.resolve().parent != prompt_root.resolve():
                raise ValueError("account_prompt_path_invalid")
            prompt = _read_bounded(prompt_path, MAX_PROMPT_BYTES, "account_prompt")
            if not prompt.strip() or _SECRET.search(prompt) or any(ord(c) < 32 and c not in "\n\r\t" for c in prompt):
                raise ValueError("account_prompt_invalid")
            self._records[key], self._prompts[key] = row, prompt
            profile_platforms.add(pair)
            if row["native_account_id"]:
                native_identities.add(native)
            # Two selected contexts fit the existing compact stage handoff.
            if len(json.dumps(self.context(key), ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES:
                raise ValueError("account_context_too_large")

    def get(self, account_id) -> dict:
        if not isinstance(account_id, str) or account_id not in self._records:
            raise ValueError("unknown_account_key")
        return dict(self._records[account_id])

    def all(self) -> list[dict]:
        return [dict(row) for row in self._records.values()]

    def binding(self, account_id) -> dict:
        row = self.get(account_id)
        if row["platform"] not in SUPPORTED:
            raise ValueError("account_transport_unsupported")
        if not row["reviewed"]:
            raise ValueError("account_binding_unreviewed")
        if not row["native_account_id"]:
            raise ValueError("account_native_identity_unverified")
        return {key: row[key] for key in ("id", "upload_post_profile", "platform", "native_account_id")}

    def context(self, account_id) -> dict:
        row = self.get(account_id)
        return {"account_key": row["id"], "platform": row["platform"], "role": row["role"],
                "language": row["language"], "approach": row["approach"], "voice": row["voice"],
                "prompt": self._prompts[account_id].strip()}

    def fingerprint(self) -> str:
        snapshot = [{"account": self._records[key], "prompt": self._prompts[key]}
                    for key in sorted(self._records)]
        return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
