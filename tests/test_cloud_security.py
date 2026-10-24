"""Staging security regressions: no network, no live credentials or Cloud jobs."""
import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gtm.__main__ import main
from gtm.config import Config
from gtm.engine import Engine, GuardError
from gtm.publisher import UploadPostPublisher, ValidationError, _http_transport
from gtm.safety import posting_locked, publication_lock
from gtm.store import Store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cloud_security", ROOT / "scripts/cloud-security.py")
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


class StagingLockTests(unittest.TestCase):
    def test_writes_block_before_injected_transport_but_get_is_allowed(self):
        transport = Mock(return_value={"status_code": 200, "body": {"fixture": True}})
        publisher = UploadPostPublisher("offline-fixture-key", "fixture-profile", transport)
        with patch.dict(os.environ, {}, clear=True), publication_lock(True):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(method=method), self.assertRaisesRegex(ValidationError, "cloud_posting_locked"):
                    publisher._call(method, "/fixture")
            transport.assert_not_called()
            self.assertEqual(publisher._call("GET", "/fixture"), {"fixture": True})
        self.assertEqual(transport.call_count, 1)

    def test_real_transport_has_independent_guard_before_network(self):
        with publication_lock(True), patch("urllib.request.build_opener") as opener:
            with self.assertRaisesRegex(ValidationError, "cloud_posting_locked"):
                _http_transport("POST", "https://api.upload-post.com/api/upload_text", {}, b"fixture")
        opener.assert_not_called()

    def test_all_environment_values_lock_and_nested_context_cannot_unlock(self):
        for value in ("1", "0", "", "invalid"):
            with patch.dict(os.environ, {"GTM_POSTING_LOCK": value}, clear=True):
                self.assertTrue(posting_locked())
        with patch.dict(os.environ, {}, clear=True):
            with publication_lock(True):
                with publication_lock(False):
                    self.assertTrue(posting_locked())
            self.assertFalse(posting_locked())

    def test_engine_locked_even_when_configuration_enables_posting(self):
        config = Config()
        config.publishing.auto_publish = True
        config.publishing.dry_run = False
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "fixture.sqlite")
            try:
                engine = Engine(config, store, publisher=Mock(simulated=False))
                with publication_lock(True), self.assertRaisesRegex(GuardError, "staging lock"):
                    engine.publishing_guard(datetime.now(timezone.utc))
            finally:
                store.close()

    def test_cli_cannot_start_authorize_approve_or_resume_in_staging(self):
        for command in (["start", "--at", "2026-10-24T00:00:00Z"], ["approve", "fixture"],
                        ["authorize", "--account-user", "fixture"], ["resume"]):
            with self.subTest(command=command[0]), patch.dict(os.environ, {}, clear=True), \
                    patch("gtm.__main__.Store") as store, patch("socket.socket") as network, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(ROOT / "config.toml"), "--no-publish", *command]), 2)
                store.assert_not_called()
                network.assert_not_called()
                self.assertFalse(posting_locked())


class CloudHostChecksTests(unittest.TestCase):
    def test_unknown_rate_does_not_become_free_and_overbudget_is_blocked(self):
        self.assertIsNone(security.budget_plan()["projected_usd"])
        self.assertEqual(security.budget_plan("0.10", "0.05")["status"], "blocked_over_envelope")
        result = security.budget_plan("0.05", "0.05")
        self.assertEqual(result["projected_usd"], 15.75)
        self.assertFalse(result["provider_billing_cap_verified"])
        self.assertFalse(result["auto_shutdown_installed"])

    def test_invalid_rates_and_retention_fail_closed(self):
        for hourly, stopped, days in (("NaN", "0", 7), ("Infinity", "0", 7), ("-1", "0", 7),
                                     ("0", "0", 7), (".01", "-.1", 7), (".01", "0", -1),
                                     (".01", "0", True)):
            with self.subTest(hourly=hourly), self.assertRaises(security.checks.ValidationError):
                security.budget_plan(hourly, stopped, days)

    def test_secret_file_names_only_and_unsafe_permissions_symlinks_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.env"
            path.write_text("UPLOAD_POST_API_KEY=offline-fixture-private\n")
            path.chmod(0o600)
            self.assertEqual(security.credential_names(path), {"UPLOAD_POST_API_KEY"})
            path.chmod(0o644)
            with self.assertRaises(security.checks.ValidationError):
                security.credential_names(path)
            link = Path(directory) / "linked.env"
            link.symlink_to(path)
            self.assertFalse(security.private_metadata(link))
            with self.assertRaises(OSError):
                security.credential_names(link)

    def test_cli_failure_omits_paths_and_secret_content(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = security.main(["--root", directory])
            self.assertEqual(result, 1)
            self.assertNotIn(directory, output.getvalue())
            self.assertFalse(json.loads(output.getvalue())["cloud_deployed"])

    def test_host_audit_is_read_only_and_missing_checks_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("state", "reports", "hermes", "runtime"):
                (root / name).mkdir(mode=0o700)
            secret = root / "state/runtime.env"
            secret.write_text("")
            secret.chmod(0o600)
            before = sorted(str(path) for path in root.rglob("*"))
            fake = {"default_guards": {}, "hermes_runtime": {"checked": False}}
            with patch.object(security.checks, "preflight", return_value=fake), \
                    patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                    patch.dict(os.environ, {}, clear=True), patch.object(security.platform, "system", return_value="Linux"):
                result = security.inspect_host(root)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["live_activation_ready"])
                self.assertEqual(result["budget_plan"]["status"], "blocked_missing_current_prices")
            self.assertEqual(before, sorted(str(path) for path in root.rglob("*")))

    def test_profile_and_price_failures_block_even_on_private_linux_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("state", "reports", "hermes", "runtime"):
                (root / name).mkdir(mode=0o700)
            secret = root / "state/runtime.env"
            secret.write_text("")
            secret.chmod(0o600)
            fake = {"default_guards": {}, "hermes_runtime": {"checked": True}}
            with patch.object(security.checks, "preflight", return_value=fake), \
                    patch.dict(os.environ, {}, clear=True), patch.object(security.platform, "system", return_value="Linux"), \
                    patch.object(security.os, "geteuid", return_value=1000):
                args = (root, root / "hermes", root / "runtime")
                result = security.inspect_host(*args, hourly="0.05", stopped_daily="0.05")
                self.assertEqual(result["status"], "staging_checks_passed")
                self.assertFalse(result["cloud_identity_verified"])
                self.assertFalse(result["live_activation_ready"])
                self.assertEqual(security.inspect_host(*args)["status"], "blocked")
                env = root / "hermes/.env"
                env.write_text("UPLOAD_POST_API_KEY=offline-fixture-key\n")
                env.chmod(0o600)
                result = security.inspect_host(*args, hourly="0.05", stopped_daily="0.05")
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["hermes_profile_checks"]["hermes_home"], "blocked_mixed_credentials")
                self.assertNotIn("offline-fixture-key", json.dumps(result))
