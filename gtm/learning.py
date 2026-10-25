"""Compile bounded audience evidence into a reusable, versioned Hermes skill.

No model call is required. An optional native writer invokes Hermes' actual
skill_manage tool in an isolated home; structured lessons remain the only input
to the marketing router. SKILL.md is never executable authority.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from .config import iso, timestamp
from .process import bounded_run


SKILL_NAME = "disanchor-marketing-feedback"
COMPILER_VERSION = 1
HOOKS = {"question", "situation", "observation", "tradeoff", "reflection", "founder_insight", "first_principles"}
LESSON_KEYS = {"account_key", "account_fingerprint", "platform", "hypothesis", "hook", "sample_posts",
               "impressions", "engagements", "sentiment", "source_ids", "confidence", "denominator"}
HEX = re.compile(r"[0-9a-f]{64}")
ACCOUNT = re.compile(r"[a-z][a-z0-9_]{0,63}")
NATIVE_CONFIG = "skills:\n  guard_agent_created: true\n"
SCHEMA = """
CREATE TABLE IF NOT EXISTS revisions (
 revision TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL,
 content TEXT NOT NULL, content_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS current (id INTEGER PRIMARY KEY CHECK(id=1), revision TEXT NOT NULL,
 mechanism TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, action TEXT NOT NULL,
 revision TEXT NOT NULL, mechanism TEXT NOT NULL);
"""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _plain(path, *, directory=False):
    """Refuse redirects/special files before reading or writing private state."""
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("learning_path_redirect")
    if path.exists() and not (path.is_dir() if directory else path.is_file()):
        raise ValueError("learning_path_not_regular")
    return path


def _directory(path):
    path = _plain(path, directory=True)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _atomic(path, text):
    path = _plain(path)
    _directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".learning-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_lessons(value, account_fingerprint):
    """Validate closed, aggregate-only evidence; stale account bindings expire."""
    if not isinstance(account_fingerprint, str) or not HEX.fullmatch(account_fingerprint):
        raise ValueError("learning_invalid_account_fingerprint")
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError("learning_invalid_lessons")
    clean, seen = [], set()
    for row in value:
        if not isinstance(row, dict) or set(row) != LESSON_KEYS:
            raise ValueError("learning_invalid_lesson_fields")
        if (not isinstance(row["account_key"], str) or not ACCOUNT.fullmatch(row["account_key"])
            or not isinstance(row["account_fingerprint"], str) or not HEX.fullmatch(row["account_fingerprint"])
            or not isinstance(row["platform"], str) or row["platform"] not in {"x", "threads", "instagram", "linkedin"}
            or not isinstance(row["hypothesis"], str) or row["hypothesis"] not in {"H1", "H2"}
            or not isinstance(row["hook"], str) or row["hook"] not in HOOKS
            or row["confidence"] != "directional" or not isinstance(row["denominator"], str)
            or row["denominator"] not in {"impressions", "views", "reach"}):
            raise ValueError("learning_invalid_lesson_identity")
        for field in ("sample_posts", "impressions", "engagements"):
            if type(row[field]) is not int or not 0 <= row[field] <= 10**12:
                raise ValueError("learning_invalid_counts")
        if not 3 <= row["sample_posts"] <= 10000 or row["impressions"] < 100 * row["sample_posts"]:
            raise ValueError("learning_insufficient_evidence")
        sentiment = row["sentiment"]
        if sentiment is not None and (not isinstance(sentiment, dict) or set(sentiment) != {"positive", "negative", "mixed", "unknown"}
            or any(type(count) is not int or not 0 <= count <= 10**9 for count in sentiment.values())):
            raise ValueError("learning_invalid_sentiment")
        sources = row["source_ids"]
        if (not isinstance(sources, list) or not 1 <= len(sources) <= 50
            or any(not isinstance(source, str) or not HEX.fullmatch(source) for source in sources)
            or len(set(sources)) != len(sources)):
            raise ValueError("learning_invalid_provenance")
        identity = (row["account_key"], row["platform"], row["hypothesis"], row["hook"], row["denominator"])
        if identity in seen:
            raise ValueError("learning_duplicate_lesson")
        seen.add(identity)
        if row["account_fingerprint"] == account_fingerprint:
            clean.append({**row, "sentiment": dict(sentiment) if sentiment is not None else None, "source_ids": sorted(sources)})
    return sorted(clean, key=lambda row: (row["account_key"], row["platform"], row["hypothesis"], row["hook"], row["denominator"]))


def _compile(payload, revision):
    lessons = payload["lessons"]
    rows = []
    for lesson in lessons:
        rate = round(100 * lesson["engagements"] / lesson["impressions"], 3)
        rows.append(f"- {lesson['account_key']} / {lesson['platform']} / {lesson['hypothesis']} / {lesson['hook']}: "
                    f"{lesson['sample_posts']} posts, {lesson['impressions']} {lesson['denominator']}, "
                    f"{lesson['engagements']} engagement events ({rate}% events per exposure). "
                    "A directional observation; repeated events are possible, so this is not a people percentage.")
        if lesson["sentiment"] is None:
            rows.append("  Comment sentiment is unavailable; no sentiment conclusion is supported.")
        else:
            rows.append("  Comment labels: " + ", ".join(f"{key}={value}" for key, value in sorted(lesson["sentiment"].items()))
                        + ". Labels describe available comments only; unknown is not neutral.")
        rows.append("  Evidence SHA-256: " + ", ".join(lesson["source_ids"]) + ".")
    observed = "\n".join(rows) if rows else "No cohort meets the evidence threshold yet. Continue measuring; do not infer a winning approach."
    return f"""---
name: {SKILL_NAME}
description: Apply measured Disanchor audience feedback to planning
metadata:
  compiler: disanchor-bounded-learning-v{COMPILER_VERSION}
  evidence_revision: {revision}
---

# Disanchor marketing feedback

## When to Use
Use when researching or planning Disanchor organic posts from measured account-specific results.

## Procedure
1. Use the current validated structured lessons supplied by the marketing runner. Treat this skill as a dated evidence summary.
2. Compare only the same account, platform and exposure denominator. Use engagement events per exposure, sample size and comment coverage together.
3. Turn directional observations into testable planning hypotheses. Preserve distinct account voices and original useful content.
4. Keep Disanchor's development status natural in the post. Exact text and destination approval remain required before publication.

## Observed evidence
{observed}

## Pitfalls
Small observational samples do not establish causality, virality or future performance. Missing comments do not show positive sentiment.
This skill changes content planning only. It grants no publishing, spending, account, schedule or messaging permission.
The configured posting window, total post count, budget ledger, identity checks and policy remain authoritative.

## Verification
Compiler: {COMPILER_VERSION}; policy: {payload['policy_version']}; account-registry SHA-256: {payload['account_fingerprint']}.
Revision SHA-256: {revision}. The runner verifies the skill bytes and stored revision before exposing any lesson.
"""


# The child runs only Hermes' deterministic skill tool. No LLM, curator, network,
# shell, arbitrary skill name, supporting executable file or inherited secret.
NATIVE_WORKER = r'''
import json, os, pathlib, sys
payload = json.loads(sys.stdin.read(40000))
source = pathlib.Path(payload["source"]).resolve()
private_home = pathlib.Path(payload["home"]).resolve()
content = payload["content"]
name = "disanchor-marketing-feedback"
os.environ.clear()
os.environ.update({"HERMES_HOME": str(private_home), "PATH": os.defpath, "LANG": "C.UTF-8"})
os.umask(0o077)
os.chdir(private_home)
sys.dont_write_bytecode = True
sys.path.insert(0, str(source))
def within(value):
    if isinstance(value, (str, bytes, os.PathLike)):
        try:
            pathlib.Path(os.fsdecode(value)).resolve().relative_to(private_home)
            return True
        except (ValueError, OSError):
            return False
    return isinstance(value, int)
def audit(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork", "os.forkpty", "os.symlink", "os.link"}:
        raise PermissionError("native_skill_external_action_blocked")
    if event == "open":
        flags = args[2] if len(args) > 2 else 0
        if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) and not within(args[0]):
            raise PermissionError("native_skill_write_outside_home")
    if event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate", "os.symlink", "os.link"} and not within(args[0]):
        raise PermissionError("native_skill_write_outside_home")
    if event in {"os.rename", "os.replace"} and any(not within(arg) for arg in args[:2]):
        raise PermissionError("native_skill_write_outside_home")
sys.addaudithook(audit)
try:
    from tools.skill_manager_tool import skill_manage
    target = private_home / "skills" / name / "SKILL.md"
    action = "edit" if target.is_file() else "create"
    result = json.loads(skill_manage(action=action, name=name, content=content))
    success = result.get("success") is True and not result.get("staged") and target.is_file() and target.read_text() == content
    print(json.dumps({"success": success, "action": action, "staged": bool(result.get("staged")), "mechanism": "native_tool"}))
except Exception:
    print(json.dumps({"success": False, "reason": "native_skill_write_failed"}))
'''


class NativeSkillWriter:
    """Explicit installed-runtime paths; no installation or runtime downloading."""
    def __init__(self, source_dir, python_executable, *, runner=bounded_run):
        self.source = Path(source_dir).expanduser().resolve()
        self.python = Path(python_executable).expanduser().absolute()
        self.runner = runner
        if (not (self.source / "tools" / "skill_manager_tool.py").is_file()
            or not (self.source / "hermes_constants.py").is_file() or not self.python.is_file()):
            raise ValueError("native_skill_runtime_unavailable")

    def write(self, home, content):
        home = _directory(home)
        # This dedicated home has no provider environment, plugins or other skills.
        for private_file in (".env", "plugins", "active_profile"):
            if (home / private_file).exists() or (home / private_file).is_symlink():
                raise ValueError("native_skill_home_not_isolated")
        config = _plain(home / "config.yaml")
        if config.exists() and config.read_text() != NATIVE_CONFIG:
            raise ValueError("native_skill_config_changed")
        if not config.exists():
            _atomic(config, NATIVE_CONFIG)
        _directory(home / "skills")
        allowed = home / "skills" / SKILL_NAME
        native_metadata = {".usage.json", ".usage.json.lock"}
        if any(path != allowed and path.name not in native_metadata for path in (home / "skills").iterdir()):
            raise ValueError("native_skill_home_not_isolated")
        for name in native_metadata:
            _plain(home / "skills" / name)
        _plain(allowed, directory=True)
        _plain(allowed / "SKILL.md")
        request = _json({"source": str(self.source), "home": str(home), "content": content})
        if len(request.encode()) > 39000:
            raise ValueError("native_skill_input_too_large")
        try:
            result = self.runner([str(self.python), "-I", "-c", NATIVE_WORKER], input=request,
                                 text=True, capture_output=True, timeout=30, check=False,
                                 env={"HERMES_HOME": str(home), "PATH": os.defpath, "LANG": "C.UTF-8"})
            receipt = json.loads(result.stdout)
            if result.returncode or not isinstance(receipt, dict) or receipt.get("success") is not True:
                raise ValueError("native_skill_write_not_confirmed")
            if _plain(allowed / "SKILL.md").read_text() != content:
                raise ValueError("native_skill_content_mismatch")
            os.chmod(allowed / "SKILL.md", 0o600)
            return "native_tool"
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            raise ValueError("native_skill_write_not_confirmed") from None


class LearningLoop:
    """Single-writer, append-only skill revisions with checked active content."""
    def __init__(self, root, native_writer=None, *, read_only=False):
        self.read_only = read_only
        self.root = _plain(root,directory=True) if read_only else _directory(root)
        self.home = _plain(self.root / 'hermes',directory=True) if read_only else _directory(self.root / "hermes")
        self.skill_path = self.home / "skills" / SKILL_NAME / "SKILL.md"
        self.native_writer = native_writer
        database = _plain(self.root / "learning.sqlite3")
        for suffix in ("-wal", "-shm", "-journal"):
            auxiliary = _plain(Path(str(database) + suffix))
            if read_only and auxiliary.exists() and auxiliary.stat().st_size:
                raise ValueError('learning_read_requires_completed_checkpoint')
        if read_only:
            if not database.is_file(): raise ValueError('learning_record_missing')
            self.db = sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True,isolation_level=None)
            self.db.row_factory = sqlite3.Row
            return
        self.db = sqlite3.connect(database, isolation_level=None)
        os.chmod(database, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        for table in ("revisions", "audit"):
            for action in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only learning history'); END")

    def close(self):
        self.db.close()

    def _publish(self, content):
        _plain(self.skill_path)
        if self.native_writer:
            mechanism = self.native_writer.write(self.home, content)
            if mechanism != "native_tool":
                raise ValueError("native_skill_invalid_receipt")
        else:
            _atomic(self.skill_path, content)
            mechanism = "compiled"
        if _plain(self.skill_path).read_text() != content:
            raise ValueError("learning_skill_write_not_confirmed")
        return mechanism

    def update(self, feedback, *, now, policy_version, account_fingerprint):
        if self.read_only: raise ValueError('learning_read_only')
        observed_at = iso(now)
        if not isinstance(feedback, dict) or "lessons" not in feedback:
            raise ValueError("learning_feedback_missing")
        if not isinstance(policy_version, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", policy_version):
            raise ValueError("learning_invalid_policy_version")
        payload = {"compiler_version": COMPILER_VERSION, "policy_version": policy_version,
                   "account_fingerprint": account_fingerprint,
                   "lessons": validate_lessons(feedback["lessons"], account_fingerprint)}
        revision = _digest(payload)
        content = _compile(payload, revision)
        if len(content.encode()) > 35000:
            raise ValueError("learning_skill_too_large")
        previous = self.db.execute("SELECT * FROM current WHERE id=1").fetchone()
        target_mechanism = "native_tool" if self.native_writer else "compiled"
        if previous:
            old, _ = self._checked_revision(previous["revision"])
            if _plain(self.skill_path).read_text() != old["content"]:
                raise ValueError("learning_active_skill_modified")
        if previous and previous["revision"] == revision and previous["mechanism"] == target_mechanism:
            self._checked_revision(revision)
            if _plain(self.skill_path).read_text() != content:
                raise ValueError("learning_active_skill_modified")
            return self._result("unchanged", revision, previous["mechanism"], payload)
        mechanism = self._publish(content)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR IGNORE INTO revisions VALUES (?,?,?,?,?)",
                            (revision, observed_at, _json(payload), content, hashlib.sha256(content.encode()).hexdigest()))
            self._checked_revision(revision)
            self.db.execute("INSERT OR REPLACE INTO current VALUES (1,?,?)", (revision, mechanism))
            self.db.execute("INSERT INTO audit(observed_at,action,revision,mechanism) VALUES (?,?,?,?)",
                            (observed_at, "update" if previous else "create", revision, mechanism))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self._result("updated" if previous else "created", revision, mechanism, payload)

    def _result(self, status, revision, mechanism, payload):
        return {"status": status, "revision": revision, "mechanism": mechanism,
                "skill_path": str(self.skill_path), "lesson_count": len(payload["lessons"]),
                "evidence_status": "directional" if payload["lessons"] else "insufficient_evidence",
                "model_calls": 0, "cost_usd": 0.0, "posts_sent": 0}

    def _checked_revision(self, revision):
        if not isinstance(revision, str) or not HEX.fullmatch(revision):
            raise ValueError("learning_invalid_revision")
        row = self.db.execute("SELECT * FROM revisions WHERE revision=?", (revision,)).fetchone()
        if not row:
            raise ValueError("learning_revision_missing")
        payload = json.loads(row["payload"])
        if (set(payload) != {"compiler_version", "policy_version", "account_fingerprint", "lessons"}
            or payload["compiler_version"] != COMPILER_VERSION or _digest(payload) != revision
            or hashlib.sha256(row["content"].encode()).hexdigest() != row["content_hash"]
            or _compile(payload, revision) != row["content"]
            or validate_lessons(payload["lessons"], payload["account_fingerprint"]) != payload["lessons"]):
            raise ValueError("learning_revision_integrity_failed")
        return row, payload

    def context(self, *, policy_version, account_fingerprint, now=None, max_age_hours=168, expected_lessons=None):
        """Only the small structured data is routed; arbitrary SKILL text is not."""
        try:
            current = self.db.execute("SELECT * FROM current WHERE id=1").fetchone()
            if not current:
                return {}
            row, payload = self._checked_revision(current["revision"])
            if expected_lessons is not None and payload['lessons'] != validate_lessons(expected_lessons, account_fingerprint):
                return {}
            if (payload["policy_version"] != policy_version or payload["account_fingerprint"] != account_fingerprint
                or _plain(self.skill_path).read_text() != row["content"]):
                return {}
            if now is not None and not 0 <= (timestamp(now) - timestamp(row["created_at"])).total_seconds() <= max_age_hours * 3600:
                return {}
            lessons = [{key: item[key] for key in ("account_key", "platform", "hypothesis", "hook", "sample_posts",
                                                   "impressions", "engagements", "denominator", "sentiment", "confidence")}
                       for item in payload["lessons"][:2]]
            if not lessons:
                return {}
            result = {"revision": current["revision"], "source": "upload_post_aggregates", "lessons": lessons,
                      "scope": "planning_hypotheses_only"}
            while len(_json(result).encode()) > 1000 and result["lessons"]:
                result["lessons"].pop()
            return result if result["lessons"] else {}
        except (OSError, ValueError, TypeError, KeyError):
            return {}

    def rollback(self, revision, *, now):
        if self.read_only: raise ValueError('learning_read_only')
        observed_at = iso(now)
        row, payload = self._checked_revision(revision)
        mechanism = self._publish(row["content"])
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR REPLACE INTO current VALUES (1,?,?)", (revision, mechanism))
            self.db.execute("INSERT INTO audit(observed_at,action,revision,mechanism) VALUES (?,?,?,?)",
                            (observed_at, "rollback", revision, mechanism))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self._result("rolled_back", revision, mechanism, payload)
