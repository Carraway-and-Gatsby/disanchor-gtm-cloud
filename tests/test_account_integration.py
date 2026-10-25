"""Destination approval and explicit credential loading, with no real HTTP/secrets."""
import contextlib
from datetime import datetime
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from gtm.__main__ import live_engine, main
from gtm.accounts import AccountRegistry
from gtm.bound_publisher import AccountBoundPublisher
from gtm.config import Config, UTC, iso, slots, timestamp
from gtm.engine import Engine, GuardError
from gtm.environment import load_private_environment
from gtm.policy import tracked_link
from gtm.store import SCHEMA, Store


FAKE_KEY = "integration-fixture-not-a-real-credential"
NOW = timestamp("2026-10-24T12:00:00Z")


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class IdentityTransport:
    """A provider that can reconnect an account between quota and submission."""
    def __init__(self):
        self.calls = []
        self.native_id = "native-builder-x"
        self.change_during_quota = False

    def __call__(self, method, url, headers, body):
        path = urlsplit(url).path
        fields = parse_qs(body.decode()) if body else None
        self.calls.append((method, path, fields))
        if method == "GET" and "/users/" in path:
            profile = unquote(path.rsplit("/", 1)[-1])
            if profile == "builder_profile":
                accounts = {"x": {"username": self.native_id, "handle": "builder", "reauth_required": False}}
            elif profile == "brand_profile":
                accounts = {"threads": {"username": "native-brand-threads", "handle": "brand", "reauth_required": False}}
            else:
                raise AssertionError("Unexpected profile")
            result = {"success": True, "profile": {"username": profile, "social_accounts": accounts}}
        elif method == "GET" and path.endswith("/uploadposts/me"):
            if self.change_during_quota:
                self.native_id = "reconnected-different-x"
            result = {"success": True, "plan": "basic", "api_usage": {"count": 0, "limit": 10}}
        elif method == "POST" and path.endswith("/upload_text"):
            result = {"success": True, "job_id": "fixture-" + fields["platform[]"][0]}
        else:
            raise AssertionError("Unexpected provider operation")
        return {"status_code": 200, "headers": {}, "body": result}


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Real HTTP forbidden")))
        self.enterContext(patch("gtm.publisher.datetime", FixedDateTime))
        prompt_dir = self.root / "prompts" / "accounts"
        prompt_dir.mkdir(parents=True)
        self.records = []
        for key, profile, platform, native, role in (
            ("builder_x", "builder_profile", "x", "native-builder-x", "builder"),
            ("brand_threads", "brand_profile", "threads", "native-brand-threads", "brand"),
            ("other_x", "other_profile", "x", "native-other-x", "official"),
        ):
            prompt_file = f"prompts/accounts/{key}.md"
            (self.root / prompt_file).write_text("Share a grounded workflow observation. Disanchor is in development.\n")
            self.records.append({"id": key, "upload_post_profile": profile, "platform": platform,
                                 "native_account_id": native, "handle": key, "role": role,
                                 "language": "en", "approach": "Useful workflow observations", "voice": "Curious and factual",
                                 "prompt_file": prompt_file, "reviewed": True})
        self.registry_path = self.root / "accounts.toml"
        self.write_registry()
        self.c = Config()
        self.c.accounts.registry_file = str(self.registry_path)
        self.c.accounts.x_account = "builder_x"
        self.c.accounts.threads_account = "brand_threads"
        self.c.publishing.auto_publish = True
        self.c.publishing.dry_run = False
        self.c.publishing.account_remaining_quota = 10
        self.c.publishing.account_user = "reviewed-owner"
        self.c.publishing.account_reviewed = True
        self.c.publishing.x_profile_url = tracked_link("https://disanchor.com", "x", "profile")
        self.c.budget.billing_verified = True
        self.c.budget.reconciled_at = iso(NOW)
        self.c.budget.infrastructure_hourly_usd = .001
        self.transport = IdentityTransport()

    def write_registry(self):
        self.registry_path.write_text("\n".join(
            "[[accounts]]\n" + "\n".join(f"{key} = {json.dumps(value)}" for key, value in row.items())
            for row in self.records))

    def store(self, path=None):
        store = Store(path or self.root / "state.sqlite")
        self.addCleanup(store.close)
        return store

    def engine(self, *, authorize=True, store=None):
        publisher = AccountBoundPublisher(FAKE_KEY, AccountRegistry(self.registry_path),
                                          {"x": self.c.accounts.x_account, "threads": self.c.accounts.threads_account},
                                          transport=self.transport)
        engine = Engine(self.c, store or self.store(), publisher=publisher)
        engine.init(NOW)
        engine.start(NOW)
        if authorize:
            self.authorize(engine)
        return engine

    def authorize(self, engine):
        engine.authorize(NOW, reviewed_policy=True, approve_library=True, account_user="reviewed-owner")

    def draft(self, engine, index=0):
        return engine.draft(slots(self.c, NOW)[index], NOW)

    def assert_no_reservation(self, engine):
        self.assertEqual(engine.s.rows("SELECT * FROM publishing_jobs"), [])


class AccountEngineIntegrationTests(FixtureCase):
    def test_korean_account_blocks_english_seed_before_saving_or_provider_calls(self):
        for account in self.records:
            if account["id"] == "brand_threads":
                account["language"] = "ko"
        self.write_registry()
        engine = self.engine(authorize=False)
        threads_index = next(i for i, slot in enumerate(slots(self.c, NOW)) if slot["platform"] == "threads")
        with self.assertRaisesRegex(ValueError, "Korean"):
            self.draft(engine, threads_index)
        self.assertEqual(engine.s.rows("SELECT * FROM post_drafts"), [])
        self.assert_no_reservation(engine)
        self.assertEqual(self.transport.calls, [])

    def test_legacy_database_adds_null_bindings_and_cannot_approve_or_schedule(self):
        current = self.engine(authorize=False)
        draft = self.draft(current)
        legacy_path = self.root / "legacy.sqlite"
        with sqlite3.connect(legacy_path) as legacy:
            legacy.executescript(SCHEMA.replace(" account_key TEXT, account_fingerprint TEXT,\n", ""))
            for table, record in (("experiments", current.experiment()), ("post_drafts", draft)):
                record = {k: v for k, v in record.items() if k not in {"account_key", "account_fingerprint"}}
                columns = ",".join(record)
                legacy.execute(f"INSERT INTO {table}({columns}) VALUES({','.join('?' for _ in record)})", tuple(record.values()))
        migrated = self.store(legacy_path)
        row = migrated.one("SELECT * FROM post_drafts")
        self.assertIsNone(row["account_key"])
        self.assertIsNone(row["account_fingerprint"])
        engine = Engine(self.c, migrated, publisher=current.publisher)
        with self.assertRaisesRegex(GuardError, "Draft destination or account strategy changed"):
            engine.approve(row["id"])
        self.authorize(engine)
        with self.assertRaisesRegex(GuardError, "Draft destination or account strategy changed"):
            engine.schedule(row["id"], NOW)
        self.assertEqual(migrated.one("SELECT state FROM post_drafts")["state"], "DRAFT")
        self.assert_no_reservation(engine)
        self.assertEqual(self.transport.calls, [])

    def test_new_draft_binds_selected_identity_and_current_strategy(self):
        engine = self.engine(authorize=False)
        draft = self.draft(engine)
        self.assertEqual(draft["account_key"], "builder_x")
        self.assertEqual(draft["account_fingerprint"], engine.account_fingerprint())
        self.assertEqual(draft["state"], "DRAFT")
        engine.approve(draft["id"])
        self.assertEqual(engine.s.one("SELECT state FROM post_drafts")["state"], "APPROVED")
        self.assertEqual(self.transport.calls, [])

    def test_target_change_blocks_old_approval_even_after_new_global_authorization(self):
        engine = self.engine()
        draft = self.draft(engine)
        self.c.accounts.x_account = "other_x"
        self.authorize(engine)
        with self.assertRaisesRegex(GuardError, "Draft destination or account strategy changed"):
            engine.schedule(draft["id"], NOW)
        self.assert_no_reservation(engine)
        self.assertEqual(self.transport.calls, [])

    def test_target_change_cannot_reapprove_pending_draft(self):
        engine = self.engine(authorize=False)
        draft = self.draft(engine)
        self.c.accounts.x_account = "other_x"
        with self.assertRaisesRegex(GuardError, "Draft destination or account strategy changed"):
            engine.approve(draft["id"])
        self.assertEqual(engine.s.one("SELECT state FROM post_drafts")["state"], "DRAFT")

    def test_stale_facade_cannot_publish_new_draft_for_changed_target(self):
        engine = self.engine()
        self.c.accounts.x_account = "other_x"
        self.authorize(engine)
        draft = self.draft(engine)
        self.assertEqual((draft["account_key"], draft["state"]), ("other_x", "APPROVED"))
        # The old facade still verifies builder_profile successfully. A verified
        # identity is insufficient unless it matches this newly approved draft.
        with self.assertRaisesRegex(GuardError, "no upload reserved"):
            engine.schedule(draft["id"], NOW)
        self.assertEqual([(method, path.rsplit("/", 1)[-1]) for method, path, _ in self.transport.calls],
                         [("GET", "builder_profile")])
        self.assert_no_reservation(engine)
        self.assertEqual(engine.experiment()["status"], "PAUSED")
        self.assertEqual(engine.experiment()["incident"], "provider_account_or_rate_limit")
        self.assertIsNone(engine.experiment()["authorization_hash"])

    def test_prompt_drift_invalidates_cached_engine_and_reloaded_old_approval(self):
        engine = self.engine()
        draft = self.draft(engine)
        (self.root / "prompts/accounts/builder_x.md").write_text("Ask a concrete question about one real workflow bottleneck.\n")
        with self.assertRaisesRegex(GuardError, "Account registry changed"):
            engine.schedule(draft["id"], NOW)
        reloaded = Engine(self.c, engine.s, publisher=engine.publisher)
        self.authorize(reloaded)
        with self.assertRaisesRegex(GuardError, "Draft destination or account strategy changed"):
            reloaded.schedule(draft["id"], NOW)
        self.assert_no_reservation(reloaded)
        self.assertEqual(self.transport.calls, [])

    def test_identity_mismatch_stops_before_quota_or_reservation(self):
        engine = self.engine()
        draft = self.draft(engine)
        self.transport.native_id = "wrong-account"
        with self.assertRaisesRegex(GuardError, "no upload reserved"):
            engine.schedule(draft["id"], NOW)
        self.assertEqual([(m, p.rsplit("/", 1)[-1]) for m, p, _ in self.transport.calls], [("GET", "builder_profile")])
        self.assert_no_reservation(engine)
        self.assertEqual(engine.experiment()["status"], "PAUSED")
        self.assertIsNone(engine.experiment()["authorization_hash"])
        self.assertEqual(engine.experiment()["incident"], "provider_account_or_rate_limit")

    def test_reconnection_after_reservation_preserves_unknown_and_never_posts(self):
        engine = self.engine()
        draft = self.draft(engine)
        self.transport.change_during_quota = True
        self.assertEqual(engine.schedule(draft["id"], NOW), "UNKNOWN")
        job = engine.s.one("SELECT * FROM publishing_jobs")
        self.assertEqual((job["state"], job["reserved_quota"], job["provider_job_id"]), ("UNKNOWN", 1, None))
        self.assertEqual(engine.s.one("SELECT state FROM post_drafts")["state"], "UNKNOWN")
        self.assertEqual(engine.experiment()["status"], "PAUSED")
        self.assertIsNone(engine.experiment()["authorization_hash"])
        self.assertEqual(engine.experiment()["incident"], "provider_account_or_rate_limit")
        self.assertEqual([method for method, _, _ in self.transport.calls], ["GET", "GET", "GET"])
        self.assertEqual(json.loads(job["payload"])["destination"]["binding"]["native_account_id"], "native-builder-x")
        # Operator remediation and authorization cannot blindly resubmit an unknown job.
        engine.resume(NOW, clear_incident=True)
        self.authorize(engine)
        self.assertEqual(engine.schedule(draft["id"], NOW), "existing_job_no_resubmission")
        self.assertEqual(len(self.transport.calls), 3)
        self.assertNotIn(FAKE_KEY, json.dumps(engine.s.rows("SELECT * FROM execution_logs")))

    def test_successful_platforms_keep_independent_profile_bindings(self):
        engine = self.engine()
        for index, profile, platform in ((0, "builder_profile", "x"), (1, "brand_profile", "threads")):
            draft = self.draft(engine, index)
            self.assertEqual(engine.schedule(draft["id"], NOW), "SCHEDULED")
            calls = self.transport.calls[-4:]
            self.assertEqual([call[0] for call in calls], ["GET", "GET", "GET", "POST"])
            self.assertTrue(calls[0][1].endswith("/" + profile))
            self.assertTrue(calls[2][1].endswith("/" + profile))
            self.assertEqual(calls[3][2]["user"], [profile])
            self.assertEqual(calls[3][2]["platform[]"], [platform])
        self.assertEqual(len(engine.s.rows("SELECT * FROM publishing_jobs")), 2)


class AccountCLIIntegrationTests(FixtureCase):
    def test_live_engine_uses_bound_facade_without_legacy_single_profile(self):
        self.c.publishing.account_user = ""
        os.environ["UPLOAD_POST_API_KEY"] = FAKE_KEY
        for monitoring_only in (True, False):
            with self.subTest(monitoring_only=monitoring_only):
                engine = live_engine(self.c, self.store(self.root / f"cli-{monitoring_only}.sqlite"), monitoring_only)
                self.assertIsInstance(engine.publisher, AccountBoundPublisher)
                self.assertEqual(engine.publisher.targets, {"x": "builder_x", "threads": "brand_threads"})
                self.assertEqual(engine.publisher._profiles, {"x": "builder_profile", "threads": "brand_profile"})
        self.assertEqual(self.transport.calls, [])

    def test_missing_key_does_not_construct_a_fallback_publisher(self):
        with patch("gtm.bound_publisher.AccountBoundPublisher", side_effect=AssertionError("No credential")):
            engine = live_engine(self.c, self.store(), monitoring_only=True)
        self.assertIsNone(engine.publisher)

    def test_invalid_targets_cannot_fall_back_to_legacy_single_profile(self):
        os.environ["UPLOAD_POST_API_KEY"] = FAKE_KEY
        self.c.publishing.account_user = "legacy_profile"
        self.c.accounts.x_account = "brand_threads"
        with self.assertRaises(Exception) as caught:
            live_engine(self.c, self.store(), monitoring_only=True)
        self.assertEqual(getattr(caught.exception, "code", None), "account_target_configuration_invalid")
        self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_cli_does_not_autoload_dotenv_or_state_runtime_file(self):
        (self.root / "state").mkdir()
        for path in (self.root / ".env", self.root / "state/runtime.env"):
            path.write_text("UPLOAD_POST_API_KEY=" + FAKE_KEY + "\n")
            path.chmod(0o600)
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch("gtm.__main__.Config.load", return_value=self.c), \
                    patch("gtm.environment.load_private_environment", side_effect=AssertionError("Implicit load forbidden")), \
                    patch("gtm.account_connection.check_connection", return_value={"status": "disabled", "network_calls": 0}) as check, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["connection-check"]), 0)
            self.assertIsNone(check.call_args.kwargs["api_key"])
            self.assertNotIn(FAKE_KEY, output.getvalue())
        finally:
            os.chdir(previous)

    def test_explicit_env_file_is_loaded_before_cli_connection_check_without_echo(self):
        path = self.root / "private.env"
        path.write_text("UPLOAD_POST_API_KEY=" + FAKE_KEY + "\n")
        path.chmod(0o600)
        with patch("gtm.__main__.Config.load", return_value=self.c), \
                patch("gtm.account_connection.check_connection", return_value={"status": "fixture_read_only", "posts_sent": 0}) as check, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--env-file", str(path), "connection-check"]), 0)
        self.assertEqual(check.call_args.kwargs["api_key"], FAKE_KEY)
        self.assertNotIn(FAKE_KEY, output.getvalue())


class PrivateEnvironmentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "private.env"
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def write(self, content, mode=0o600):
        self.path.write_bytes(content.encode() if isinstance(content, str) else content)
        self.path.chmod(mode)
        return self.path

    def test_owner_only_regular_file_loads_literals_without_execution_or_returning_secrets(self):
        marker = self.root / "must-not-exist"
        literal = f"$(touch {marker})`touch {marker}`"
        self.write(f"# Credential fixture\nUPLOAD_POST_API_KEY={FAKE_KEY}\nCLONE_USER_ID={literal}\n")
        with patch("os.system", side_effect=AssertionError("No shell")), \
                patch("subprocess.Popen", side_effect=AssertionError("No subprocess")):
            result = load_private_environment(self.path)
        self.assertIsNone(result)
        self.assertEqual(os.environ["UPLOAD_POST_API_KEY"], FAKE_KEY)
        self.assertEqual(os.environ["CLONE_USER_ID"], literal)
        self.assertFalse(marker.exists())

    def test_group_or_world_permissions_are_rejected_without_loading(self):
        for mode in (0o640, 0o604, 0o660, 0o644):
            with self.subTest(mode=oct(mode)):
                self.write("UPLOAD_POST_API_KEY=" + FAKE_KEY, mode)
                with self.assertRaisesRegex(ValueError, "owner-only and regular"):
                    load_private_environment(self.path)
                self.assertNotIn("UPLOAD_POST_API_KEY", os.environ)

    def test_other_owner_is_rejected_before_reading_values(self):
        self.write("UPLOAD_POST_API_KEY=" + FAKE_KEY)
        actual = self.path.stat()
        info = SimpleNamespace(st_mode=actual.st_mode, st_uid=os.getuid() + 1)
        with patch("gtm.environment.os.fstat", return_value=info), \
                self.assertRaisesRegex(ValueError, "owner-only and regular"):
            load_private_environment(self.path)
        self.assertNotIn("UPLOAD_POST_API_KEY", os.environ)

    def test_symlink_directory_and_fifo_are_rejected(self):
        self.write("UPLOAD_POST_API_KEY=" + FAKE_KEY)
        link = self.root / "linked.env"
        link.symlink_to(self.path)
        fifo = self.root / "fifo.env"
        os.mkfifo(fifo, mode=0o600)
        for path in (link, self.root, fifo):
            with self.subTest(kind=path.name), self.assertRaises(ValueError) as caught:
                load_private_environment(path)
            self.assertNotIn(FAKE_KEY, str(caught.exception))
            self.assertNotIn("UPLOAD_POST_API_KEY", os.environ)

    def test_bad_fields_are_atomic_and_never_echo_values(self):
        invalid = ["UNRECOGNIZED=" + FAKE_KEY, "UPLOAD_POST_API_KEY=duplicate", "not-an-assignment",
                   "export TELEGRAM_CHAT_ID=123", " TELEGRAM_CHAT_ID=123", "TELEGRAM_CHAT_ID=12\x003",
                   "TELEGRAM_CHAT_ID=12\t3", "TELEGRAM_CHAT_ID=12\x7f3"]
        for bad in invalid:
            with self.subTest(field=bad.split("=", 1)[0]):
                os.environ["UPLOAD_POST_API_KEY"] = "original-value"
                self.write("UPLOAD_POST_API_KEY=" + FAKE_KEY + "\n" + bad + "\n")
                with self.assertRaises(ValueError) as caught:
                    load_private_environment(self.path)
                self.assertEqual(os.environ["UPLOAD_POST_API_KEY"], "original-value")
                self.assertNotIn("TELEGRAM_CHAT_ID", os.environ)
                self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_non_utf8_or_oversized_file_is_rejected_atomically(self):
        for value in (b"UPLOAD_POST_API_KEY=" + FAKE_KEY.encode() + b"\n\xff", b"#" * 32769):
            self.write(value)
            with self.assertRaises(ValueError) as caught:
                load_private_environment(self.path)
            self.assertNotIn("UPLOAD_POST_API_KEY", os.environ)
            self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_cli_invalid_env_file_fails_before_connection_and_omits_secrets(self):
        self.write("UPLOAD_POST_API_KEY=" + FAKE_KEY + "\nNOT_ALLOWED=" + FAKE_KEY)
        with patch("gtm.__main__.Config.load", return_value=Config()), \
                patch("gtm.account_connection.check_connection", side_effect=AssertionError("No connection with invalid env")), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--env-file", str(self.path), "connection-check"]), 2)
        self.assertEqual(json.loads(errors.getvalue())["status"], "blocked")
        self.assertNotIn(FAKE_KEY, errors.getvalue())
        self.assertNotIn("UPLOAD_POST_API_KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
