import contextlib
from dataclasses import replace
from datetime import timedelta
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gtm.config import Config, timestamp, slots
from gtm.engine import Engine, GuardError
from gtm.policy import validate_content, SEEDS, seed_text, require_action, valid_profile_link
from gtm.publisher import MockPublisher
from gtm.clone import MockClone
from gtm.analytics import MockGA4
from gtm.store import Store, process_lock
from gtm.reporting import export
from gtm.__main__ import dry_run, main


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.c = Config.load("config.toml")
        # Exercise the original reviewed ten-slot library independently of the
        # deployment's new six-slot weekly calendar (covered in test_schedule).
        self.c.schedule.mode = "legacy"
        self.c.platforms.max_posts_total = 10
        self.c.platforms.max_posts_x = self.c.platforms.max_posts_threads = 5
        self.c.publishing.account_remaining_quota = 10
        self.now = timestamp("2026-10-24T12:00:00Z")
        self.s = Store(self.path / "state.sqlite")
        self.addCleanup(self.s.close)
        self.e = Engine(self.c, self.s, publisher=MockPublisher(), ga4=MockGA4(), clone=MockClone(), simulated=True)
        self.e.init(self.now)
        self.e.start(self.now)

    def test_defaults_fail_closed_and_fractional_costs_supported(self):
        c = Config.load("config.toml")
        self.assertEqual(c.schedule.mode, "north_america_weekly")
        self.assertEqual(c.platforms.max_posts_total, 6)
        self.assertFalse(c.publishing.auto_publish)
        self.assertTrue(c.publishing.dry_run)
        self.assertEqual(c.publishing.account_remaining_quota, 0)
        c.budget.target_total_usd = 9.5
        c.budget.infrastructure_hourly_usd = .01
        c.validate()

    def test_disallowed_action_config(self):
        for name in ("allow_direct_messages", "allow_email", "allow_unsolicited_replies", "allow_paid_ads"):
            c = Config()
            setattr(c.publishing, name, True)
            with self.assertRaises(ValueError):
                c.validate()
        for name in ("dm", "like", "email", "browser_post", "follow", "reply", "upgrade"):
            with self.assertRaises(ValueError):
                require_action(name)

    def test_typed_config_rejects_string_false_nan_and_unknown(self):
        self.c.publishing.auto_publish = "false"
        with self.assertRaises(ValueError):
            self.c.validate()
        c = Config()
        c.budget.target_total_usd = float("nan")
        with self.assertRaises(ValueError):
            c.validate()
        path = self.path / "bad.toml"
        path.write_text("[unknown]\nfoo = 1")
        with self.assertRaises(ValueError):
            Config.load(path)

    def test_no_naive_timestamps(self):
        with self.assertRaises(ValueError):
            timestamp("2026-10-24T12:00:00")

    def test_exact_duration_across_dst(self):
        for start in ("2026-03-06T12:00:00Z", "2026-10-30T12:00:00Z"):
            planned = slots(self.c, timestamp(start))
            self.assertEqual(len(planned), 10)
            self.assertEqual(sum(p["platform"] == "x" for p in planned), 5)
            self.assertTrue(all(timestamp(start) <= timestamp(p["scheduled_at"]) < timestamp(start) + timedelta(hours=168) for p in planned))
        c = Config()
        c.platforms.threads_time = "02:30"
        with self.assertRaises(ValueError):
            slots(c, timestamp("2026-03-06T12:00:00Z"))

    def test_end_is_exclusive_no_work_and_no_resume(self):
        self.e.active(self.now + timedelta(hours=168, microseconds=-1))
        with self.assertRaises(GuardError):
            self.e.daily(self.now + timedelta(hours=168))
        self.assertEqual(self.e.experiment()["status"], "ENDED")
        with self.assertRaises(GuardError):
            self.e.resume(self.now + timedelta(hours=169))

    def test_seed_library_is_distinct_and_valid(self):
        texts = []
        for slot in slots(self.c, self.now):
            text = seed_text(slot, self.c)["text"]
            validate_content(slot["platform"], text, texts)
            texts.append(text)
        self.assertEqual(len(texts), 10)

    def test_content_blocks_false_promises_links_mentions_and_secrets(self):
        for text in ("Disanchor is available now", "@somebody try us", "Email me a@b.com", "Here: https://disanchor.com", "api_key=hidden", "guaranteed access"):
            with self.assertRaises(ValueError, msg=text):
                validate_content("x", text)
        with self.assertRaises(ValueError):
            validate_content("threads", "Visit https://attacker.example")
        with self.assertRaises(ValueError):
            validate_content("x", "same words", ["Same Words"])

    def test_profile_utm_is_channel_only(self):
        from gtm.policy import tracked_link
        link = tracked_link("https://disanchor.com", "x", "x_01")
        self.assertTrue(valid_profile_link(link, self.c.landing.campaign))
        self.assertIn("x_profile", link)

    def test_daily_rolling_queue_and_repeated_runs_idempotent(self):
        self.e.daily(self.now)
        self.e.daily(self.now)
        self.assertEqual(len(self.s.rows("SELECT * FROM post_drafts")), 2)
        self.assertEqual(len(self.s.rows("SELECT * FROM publishing_jobs")), 2)
        self.assertEqual(len(self.s.records("experiment_decisions")), 1)

    def test_clone_time_applies_single_variable(self):
        self.e.clone = MockClone(suggestion={"time": "17:30"})
        self.e.daily(self.now)
        decision = self.s.records("experiment_decisions")[0]["payload"]
        self.assertTrue(decision["accepted"])
        self.assertEqual(decision["changed"]["variable"], "publication_time")

    def test_abstention_fallback(self):
        for status in ("abstain", "error", "expired"):
            prediction = MockClone(mode=status).suggest({"goal": "test"})
            self.assertIsNone(prediction["suggestion"])

    def test_budget_warning_stop_and_unknown_billing(self):
        self.assertEqual(self.c.budget.target_total_usd, 20.0)
        self.c.budget.infrastructure_hourly_usd = 14.0 / 168
        self.assertTrue(self.e.budget(self.now)["warning"])
        self.assertFalse(self.e.budget(self.now)["halt_optional"])
        self.c.budget.infrastructure_hourly_usd = 18.0 / 168
        self.assertTrue(self.e.budget(self.now)["halt_optional"])
        self.assertTrue(self.e.budget(self.now)["unknown_charges"])

    def test_append_only_resists_replace(self):
        self.s.cost("a", self.now, "model", "confirmed", 1, "test")
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.db.execute("INSERT OR REPLACE INTO cost_ledger(event_key,observed_at,category,classification,amount_usd,source,note) VALUES('a','x','model','confirmed',0,'test','')")

    def test_cost_reconciliation_preserves_history_without_double_count(self):
        self.s.cost("unknown", self.now, "external", "pending", None, "test")
        self.s.cost("settled", self.now, "external", "confirmed", .2, "test", supersedes="unknown")
        self.assertEqual(len(self.s.rows("SELECT * FROM cost_ledger")), 2)
        self.assertEqual(self.e.budget(self.now)["external_spend_usd"], .2)
        with self.assertRaises(ValueError):
            self.s.cost("rewritten", self.now, "external", "confirmed", 0, "test", supersedes="settled")
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.db.execute("DELETE FROM cost_reconciliations")

    def test_simulated_mode_cannot_read_real_lead_store(self):
        self.c.analytics.lead_db = str(self.path / "private.sqlite")
        Path(self.c.analytics.lead_db).write_text("PRIVATE DATA MUST NOT BE READ")
        with patch("gtm.leads.aggregate_leads", side_effect=AssertionError("private data forbidden")):
            self.e.collect(self.now)
        self.assertIsNone(self.s.records("lead_aggregate_snapshots")[0]["payload"]["stored_leads"])

    def test_process_lock_is_exclusive(self):
        with process_lock(self.path / "writer"):
            with self.assertRaises(RuntimeError):
                with process_lock(self.path / "writer"):
                    pass

    def test_collection_is_sparse_and_missing_ga4_is_explicit(self):
        self.e.ga4 = None
        self.e.collect(self.now)
        result = self.e.collect(self.now + timedelta(minutes=5))
        self.assertEqual(result["status"], "unchanged_interval_skipped")
        record = self.s.records("website_metrics_snapshots")[0]["payload"]
        self.assertIsNone(record["totals"])
        self.assertEqual(record["status"], "unconfigured")

    def test_safe_report_incomplete_no_claim_of_reach(self):
        self.e.daily(self.now)
        self.e.ga4 = None
        self.e.collect(self.now)
        export(self.e, self.now, self.path / "report")
        text = (self.path / "report/final_report.md").read_text()
        self.assertIn("SIMULATED", text)
        self.assertIn("NULL", text)
        self.assertIn("INSUFFICIENT EVIDENCE", text)
        self.assertNotIn("unique people reached |", text)

    def test_end_to_end_offline_dry_run(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")):
            result = dry_run(self.c, self.path / "demo")
        db = sqlite3.connect(self.path / "demo/simulation.sqlite")
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT count(*) FROM publishing_jobs").fetchone()[0], 10)
        self.assertEqual(db.execute("SELECT status FROM experiments").fetchone()[0], "ENDED")
        self.assertEqual(db.execute("SELECT count(*) FROM experiment_decisions").fetchone()[0], 7)
        self.assertEqual(db.execute("SELECT count(*) FROM platform_posts").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT count(*) FROM routing_runs WHERE status='AWAITING_REVIEW'").fetchone()[0], 7)
        self.assertEqual(db.execute("SELECT count(*) FROM routing_stage_runs WHERE state='SUCCEEDED'").fetchone()[0], 28)
        self.assertEqual(db.execute("SELECT count(*) FROM telegram_outbox").fetchone()[0], 0)
        self.assertIn("NOT SENT TO TELEGRAM", (self.path / "demo/telegram-preview.txt").read_text())
        self.assertEqual(set(result["files"]), {"index.html", "final_report.md", "daily_metrics.csv", "post_metrics.csv", "cost_ledger.csv", "experiment_decisions.json", "routing_runs.json", "telegram-preview.txt", "marketing-feedback.json", "learning-status.json"})
        feedback = json.loads((self.path / "demo/marketing-feedback.json").read_text())
        self.assertTrue(feedback["simulated"])
        self.assertEqual(feedback["network_calls"], 0)
        self.assertIn("marketing-feedback.json", (self.path / "demo/index.html").read_text())

    def test_cli_preflight_never_uses_network(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["preflight"]), 0)
        self.assertEqual(json.loads(output.getvalue())["network_calls"], 0)

    def test_monitoring_does_not_load_broken_optional_analytics_credentials(self):
        from gtm.__main__ import live_engine
        self.c.analytics.ga4_property_id = "123456"
        with patch.dict("os.environ", {"GOOGLE_APPLICATION_CREDENTIALS": "/unavailable/private.json"}), \
             patch("gtm.analytics.GA4Client.from_service_account", side_effect=AssertionError("monitor must not load GA4")), \
             patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")):
            monitor_engine = live_engine(self.c, self.s, monitoring_only=True)
        self.assertIsNone(monitor_engine.ga4)


if __name__ == "__main__":
    unittest.main()
