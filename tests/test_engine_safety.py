"""Safety boundaries exercised without network requests or real credentials."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from gtm.config import Config, UTC, iso, slots
from gtm.engine import Engine, GuardError
from gtm.policy import tracked_link
from gtm.publisher import MockPublisher, RateLimitError
from gtm.store import Store


class FakeLivePublisher:
    simulated = False

    def __init__(self):
        self.remaining = 10
        self.raise_schedule = False
        self.quota_error = None
        self.schedule_calls = []
        self.cancel_calls = []
        self.states = {}

    def quota(self):
        if self.quota_error is not None:
            raise self.quota_error
        return {"remaining": self.remaining}

    def schedule(self, platform, text, scheduled_at, idempotency_key):
        self.schedule_calls.append((platform, text, scheduled_at, idempotency_key))
        if self.raise_schedule:
            raise TimeoutError("potentially accepted by provider")
        job_id = f"job_{len(self.schedule_calls)}"
        result = {"status": "scheduled", "provider_job_id": job_id,
                  "platform": platform, "external_id": idempotency_key, "simulated": False}
        self.states[job_id] = result
        return result

    def status(self, job_id):
        return dict(self.states[job_id])

    def history(self):
        return []

    def cancel(self, job_id):
        self.cancel_calls.append(job_id)
        # Deliberately unconfirmed: tests must not trust the DELETE return alone.
        return {"status": "canceled", "provider_job_id": job_id}


class EngineSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "experiment.sqlite3"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
        self.config = Config()
        self.config.publishing.auto_publish = True
        self.config.publishing.dry_run = False
        self.config.publishing.account_remaining_quota = 10
        self.config.publishing.account_user = "reviewed-test-account"
        self.config.publishing.account_reviewed = True
        self.config.publishing.x_profile_url = tracked_link("https://disanchor.com", "x", "profile")
        self.config.budget.billing_verified = True
        self.config.budget.reconciled_at = iso(self.now)
        self.config.budget.infrastructure_hourly_usd = .001
        self.publisher = FakeLivePublisher()
        self.engine = Engine(self.config, self.store, self.publisher)
        self.engine.init(self.now)
        self.engine.start(self.now)
        self.authorize()

    def authorize(self):
        self.engine.authorize(self.now, reviewed_policy=True, approve_library=True,
                              account_user="reviewed-test-account")

    def draft(self, index=0):
        return self.engine.draft(slots(self.config, self.now)[index], self.now)

    def test_unknown_submission_is_reserved_once_and_never_retried(self):
        self.publisher.raise_schedule = True
        post = self.draft()
        self.assertEqual(self.engine.schedule(post["id"], self.now), "UNKNOWN")
        self.assertEqual(self.engine.schedule(post["id"], self.now), "existing_job_no_resubmission")
        self.assertEqual(len(self.publisher.schedule_calls), 1)
        job = self.store.one("SELECT * FROM publishing_jobs")
        self.assertEqual(job["state"], "UNKNOWN")
        self.assertEqual(job["reserved_quota"], 1)

    def test_zero_provider_quota_blocks_even_with_configured_allowance(self):
        self.publisher.remaining = 0
        post = self.draft()
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)
        self.assertIsNone(self.store.one("SELECT * FROM publishing_jobs"))

    def test_nonfinite_provider_quota_is_not_an_allowance(self):
        self.publisher.remaining = float("nan")
        post = self.draft()
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)

    def test_quota_rate_limit_stops_before_post_instead_of_using_fallback(self):
        self.publisher.quota_error = RateLimitError("provider_rate_or_quota_limit", http_status=429)
        post = self.draft()
        try:
            self.engine.schedule(post["id"], self.now)
        except GuardError:
            pass
        self.assertFalse(self.publisher.schedule_calls)
        self.assertEqual(self.engine.experiment()["status"], "PAUSED")

    def test_unknown_provider_quota_uses_only_configured_baseline(self):
        self.config.publishing.account_remaining_quota = 1
        self.authorize()
        self.publisher.remaining = None
        first, second = self.draft(0), self.draft(1)
        self.assertEqual(self.engine.schedule(first["id"], self.now), "SCHEDULED")
        with self.assertRaises(GuardError):
            self.engine.schedule(second["id"], self.now)
        self.assertEqual(len(self.publisher.schedule_calls), 1)

    def test_unknown_provider_quota_default_zero_blocks(self):
        self.config.publishing.account_remaining_quota = 0
        self.authorize()
        self.publisher.remaining = None
        post = self.draft()
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)

    def test_dry_run_blocks_outbound_write(self):
        self.config.publishing.dry_run = True
        self.authorize()
        post = self.draft()
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)

    def test_configuration_change_invalidates_owner_authorization(self):
        post = self.draft()
        self.config.platforms.x_time = "13:00"
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)

    def test_approval_is_exact_bytes_not_lowercase_whitespace_equivalence(self):
        post = self.draft()
        changed = post["text"].swapcase().replace(" ", "  ")
        self.store.db.execute("UPDATE post_drafts SET text=? WHERE id=?", (changed, post["id"]))
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)

    def test_nonrunning_or_expired_experiment_cannot_publish(self):
        post = self.draft()
        end = self.now + timedelta(hours=168)
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], end)
        self.assertFalse(self.publisher.schedule_calls)
        self.assertEqual(self.engine.experiment()["status"], "ENDED")

    def test_queue_rejects_more_than_24_hours_in_advance(self):
        post = self.draft(2)
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)

    def test_schedule_date_must_match_audience_local_date(self):
        post = self.draft()
        self.store.db.execute("UPDATE post_drafts SET local_date='2026-10-25' WHERE id=?", (post["id"],))
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)

    def test_slot_caps_are_per_audience_calendar_date_across_dst(self):
        start = datetime(2026, 10, 30, 13, 0, tzinfo=UTC)
        planned = slots(self.config, start)
        self.assertEqual(len(planned), 10)
        pairs = [(row["platform"], row["local_date"]) for row in planned]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual(sum(row["platform"] == "x" for row in planned), 5)
        self.assertEqual(sum(row["platform"] == "threads" for row in planned), 5)
        for row in planned:
            when = datetime.fromisoformat(row["scheduled_at"])
            self.assertEqual(when.astimezone(ZoneInfo(self.config.experiment.audience_timezone)).date().isoformat(), row["local_date"])
            self.assertLess(when, start + timedelta(hours=168))

    def test_cancellation_without_status_confirmation_stays_unresolved(self):
        post = self.draft()
        self.engine.schedule(post["id"], self.now)
        result = self.engine.pause(self.now)
        self.assertEqual(result["unresolved_external_jobs"], 1)
        self.assertEqual(len(self.publisher.cancel_calls), 1)
        self.assertEqual(self.store.one("SELECT state FROM post_drafts WHERE id=?", (post["id"],))["state"], "SCHEDULED")

    def test_pause_resume_does_not_extend_experiment(self):
        before = self.engine.experiment()
        self.engine.pause(self.now)
        self.engine.resume(self.now + timedelta(hours=12))
        after = self.engine.experiment()
        self.assertEqual(after["start_at"], before["start_at"])
        self.assertEqual(after["end_at"], before["end_at"])

    def test_live_engine_rejects_mock_publisher(self):
        try:
            engine = Engine(self.config, self.store, MockPublisher(user="test"))
            engine.publishing_guard(self.now)
        except GuardError:
            pass
        else:
            self.fail("Live mode accepted the simulated publisher")

    def test_simulation_rejects_live_publisher_before_any_cancel(self):
        with self.assertRaises(GuardError):
            Engine(self.config, self.store, self.publisher, simulated=True)
        self.assertFalse(self.publisher.cancel_calls)

    def test_existing_database_cannot_switch_mode(self):
        with self.assertRaises(GuardError):
            Engine(self.config, self.store, MockPublisher(), simulated=True).experiment()

    def test_cost_ledger_cannot_be_updated_or_deleted(self):
        self.store.cost("receipt-1", self.now, "model", "recorded", .02, "fixture")
        for sql in ("UPDATE cost_ledger SET amount_usd=0", "DELETE FROM cost_ledger"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.db.execute(sql)
        self.store.cost("receipt-1", self.now, "model", "recorded", .01, "fixture")
        rows = self.store.rows("SELECT * FROM cost_ledger")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount_usd"], .02)

    def test_unknown_cost_blocks_optional_publishing(self):
        self.store.cost("unknown-charge", self.now, "external", "pending", None, "fixture")
        post = self.draft()
        with self.assertRaises(GuardError):
            self.engine.schedule(post["id"], self.now)
        self.assertFalse(self.publisher.schedule_calls)

    def test_cost_ledger_replace_cannot_bypass_append_only_trigger(self):
        self.store.cost("receipt-1", self.now, "model", "recorded", .02, "fixture")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute(
                "INSERT OR REPLACE INTO cost_ledger(event_key,observed_at,category,classification,amount_usd,source,note) "
                "VALUES(?,?,?,?,?,?,?)", ("receipt-1", iso(self.now), "model", "recorded", 0, "fixture", ""))
        self.assertEqual(self.store.one("SELECT amount_usd FROM cost_ledger")["amount_usd"], .02)

    def test_provider_success_without_public_identifiers_stays_unknown(self):
        post = self.draft()
        self.engine.schedule(post["id"], self.now)
        self.engine.apply_status(post["id"], {"status": "published", "provider_job_id": "job_1"}, self.now)
        self.assertEqual(self.store.one("SELECT state FROM post_drafts WHERE id=?", (post["id"],))["state"], "UNKNOWN")
        self.assertIsNone(self.store.one("SELECT * FROM platform_posts"))


if __name__ == "__main__":
    unittest.main()
