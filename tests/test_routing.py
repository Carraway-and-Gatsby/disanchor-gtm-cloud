"""Four-stage routing, approval and restart boundaries; no provider traffic."""
import copy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gtm.accounts import AccountRegistry
from gtm.config import Config, UTC, iso, slots
from gtm.engine import Engine, GuardError
from gtm.inference import MockInference, validate_draft
from gtm.policy import tracked_link
from gtm.routing import HermesStageRunner, MockStageRunner, StageRouter, STAGES, validate_stage
from gtm.store import Store


class FixtureRunner(MockStageRunner):
    """A provider-shaped local fixture, never an actual live API adapter."""
    def __init__(self, config, *, simulated=True, fail_stage=None, mutate=None, crash_stage=None):
        super().__init__(config, fail_stage)
        self.simulated = simulated
        self.mutate, self.crash_stage = mutate, crash_stage
        self.contexts, self.preflights = [], []

    def preflight(self, stage, route):
        self.preflights.append(stage)
        return {"status": "ready"}

    def run(self, stage, route, context):
        self.contexts.append((stage, copy.deepcopy(context)))
        if stage == self.crash_stage:
            self.calls.append((stage, route.model))
            raise KeyboardInterrupt("simulated process crash")
        result = super().run(stage, route, context)
        result.pop("simulated", None)
        if self.mutate and result.get("status") == "succeeded":
            self.mutate(stage, result["output"], context)
        return result


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.counter = 0
        self.now = datetime(2026, 10, 24, 14, tzinfo=UTC)

    def make(self, *, simulated=True, authorize=False, **runner_options):
        self.counter += 1
        config = Config()
        config.routing.enabled = True
        config.budget.billing_verified = True
        config.budget.reconciled_at = iso(self.now)
        config.budget.infrastructure_hourly_usd = .001
        config.publishing.account_user = "fixture-owner"
        config.publishing.account_reviewed = True
        config.publishing.x_profile_url = tracked_link("https://disanchor.com", "x", "profile")
        store = Store(Path(self.tmp.name) / f"routing-{self.counter}.sqlite3")
        self.addCleanup(store.close)
        engine = Engine(config, store, simulated=simulated)
        engine.init(self.now)
        engine.start(self.now)
        if authorize:
            engine.authorize(self.now, reviewed_policy=True, approve_library=True, account_user="fixture-owner")
        runner = FixtureRunner(config, simulated=simulated, **runner_options)
        router = StageRouter(engine, runner)
        return config, store, engine, runner, router

    def test_default_routes_and_resource_priority(self):
        config = Config.load(Path(__file__).resolve().parents[1] / "config.toml")
        self.assertFalse(config.routing.enabled)
        routes = [getattr(config.routing, stage) for stage in STAGES]
        self.assertEqual([r.provider for r in routes], ["openrouter"] * 4)
        self.assertEqual([r.model for r in routes], ["google/gemini-2.5-pro"] * 2 + ["google/gemini-2.5-flash-lite"] * 2)
        self.assertEqual([r.budget_share for r in routes], [.4, .4, .15, .05])
        self.assertEqual(config.budget.model_budget_usd, 5)
        self.assertEqual(config.routing.max_pipelines_per_day, 2)
        self.assertEqual(config.routing.max_stage_calls_per_day, 8)
        self.assertGreater(routes[0].max_input_tokens, routes[2].max_input_tokens)
        self.assertGreater(routes[1].max_cost_per_call_usd, routes[3].max_cost_per_call_usd)

    def test_invalid_resource_routes_are_rejected(self):
        for change in (
            lambda c: setattr(c.routing, "max_stage_calls_per_day", 12),
            lambda c: setattr(c.routing, "max_pipelines_per_day", 3),
            lambda c: setattr(c.routing.research, "provider", "auto"),
            lambda c: setattr(c.routing.research, "budget_share", .1),
            lambda c: setattr(c.routing.execution, "max_cost_per_call_usd", .5),
            lambda c: setattr(c.model, "enabled", True),
        ):
            config = Config()
            change(config)
            with self.assertRaises(ValueError):
                config.validate()

    def test_four_stages_use_pinned_models_in_order_without_publishing(self):
        config, store, engine, runner, router = self.make()
        result = router.run(self.now)
        self.assertEqual(result["status"], "AWAITING_REVIEW", result)
        self.assertEqual(runner.calls, [(stage, getattr(config.routing, stage).model) for stage in STAGES])
        self.assertFalse(result["published"])
        self.assertTrue(result["requires_owner_review"])
        self.assertEqual(len(store.rows("SELECT * FROM routing_stage_runs WHERE state='SUCCEEDED'")), 4)
        self.assertFalse(store.rows("SELECT * FROM publishing_jobs"))
        self.assertEqual(len(store.rows("SELECT * FROM post_drafts")), 2)

    def test_each_stage_receives_only_selected_account_styles_and_slot_bindings(self):
        _, store, engine, runner, router = self.make()
        original = engine.account_context
        def with_private_metadata(platform):
            return {**original(platform), "native_account_id": "private-native-id", "handle": "@private",
                    "upload_post_profile": "private-profile", "reviewed": True}
        with patch.object(engine, "account_context", side_effect=with_private_metadata):
            result = router.run(self.now)
        self.assertEqual(result["status"], "AWAITING_REVIEW", result)
        expected = {"x": "disanchor_x", "threads": "default_threads"}
        for stage, context in runner.contexts:
            self.assertEqual({slot["platform"]: slot["account_key"] for slot in context["slots"]}, expected, stage)
            self.assertEqual({style["platform"]: style["account_key"] for style in context["accounts"]}, expected, stage)
            self.assertEqual({style["role"] for style in context["accounts"]}, {"official", "brand"}, stage)
            for style in context["accounts"]:
                self.assertEqual(set(style), {"account_key", "platform", "role", "language", "approach", "voice", "prompt"})
            self.assertNotIn("private", json.dumps(context["accounts"]))
        for row in store.rows("SELECT * FROM post_drafts"):
            self.assertEqual(row["account_key"], expected[row["platform"]])
            self.assertEqual(row["account_fingerprint"], engine.accounts.fingerprint())
        for idea in store.records("content_ideas"):
            if idea["event_key"].startswith("route:"):
                self.assertEqual(idea["payload"]["account_key"], expected[idea["payload"]["platform"]])
                self.assertEqual(idea["payload"]["account_fingerprint"], engine.accounts.fingerprint())

    def test_selected_account_change_creates_new_route_without_transferring_approval(self):
        config, store, engine, runner, router = self.make(authorize=True)
        approved = engine.draft(slots(config, self.now)[0], self.now)
        first = router.run(self.now)
        config.accounts.x_account = "default_x"
        second = router.run(self.now)
        self.assertEqual(second["status"], "AWAITING_REVIEW", second)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(runner.calls), 8)
        changed = runner.contexts[4][1]
        self.assertEqual(changed["slots"][0]["account_key"], "default_x")
        self.assertEqual(changed["accounts"][0]["role"], "brand")
        current = store.one("SELECT * FROM post_drafts WHERE id=?", (approved["id"],))
        self.assertEqual(current, approved)
        with self.assertRaises(GuardError):
            engine.check_draft_account(current)

    def test_registry_prompt_change_invalidates_route_identity_after_reload(self):
        config, store, engine, runner, router = self.make()
        root = Path(__file__).resolve().parents[1]
        registry = Path(self.tmp.name) / "accounts.example.toml"
        shutil.copyfile(root / "accounts.example.toml", registry)
        shutil.copytree(root / "prompts" / "accounts", registry.parent / "prompts" / "accounts")
        config.accounts.registry_file = str(registry)
        engine.accounts = AccountRegistry(registry)
        first = router.run(self.now)
        before = engine.accounts.fingerprint()
        prompt = registry.parent / "prompts" / "accounts" / "disanchor_x.md"
        prompt.write_text(prompt.read_text().replace("Explain one technical constraint", "Explore one technical constraint"))
        with self.assertRaises(GuardError):
            router.run(self.now)
        self.assertEqual(len(runner.calls), 4)
        engine.accounts = AccountRegistry(registry)
        second = router.run(self.now)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotEqual(before, engine.accounts.fingerprint())
        self.assertEqual(len(runner.calls), 8)
        self.assertEqual(store.one("SELECT account_fingerprint FROM post_drafts WHERE id='x_01'")["account_fingerprint"], engine.accounts.fingerprint())

    def test_account_change_mid_pipeline_stops_before_next_call_or_draft_write(self):
        for changed_after in ("research", "execution"):
            config, store, _, runner, router = self.make()
            def mutate(stage, output, context):
                if stage == changed_after:
                    config.accounts.x_account = "default_x"
            runner.mutate = mutate
            result = router.run(self.now)
            self.assertEqual(result["status"], "STOPPED", result)
            self.assertEqual(len(runner.calls), 1 if changed_after == "research" else 4)
            self.assertFalse(store.rows("SELECT * FROM post_drafts"))
            self.assertFalse(store.rows("SELECT * FROM publishing_jobs"))

    def test_model_cannot_override_host_account_binding(self):
        def mutate(stage, output, context):
            if stage == "writing":
                output["drafts"][0]["account_key"] = "personal_x"
        _, store, _, runner, router = self.make(mutate=mutate)
        result = router.run(self.now)
        self.assertIn(result["status"], {"FAILED", "UNKNOWN"})
        self.assertEqual([stage for stage, _ in runner.calls], ["research", "planning", "writing"])
        self.assertFalse(store.rows("SELECT * FROM post_drafts"))
        self.assertFalse(store.rows("SELECT * FROM publishing_jobs"))

    def test_single_mock_drafts_use_natural_development_context(self):
        for platform in ("x", "threads"):
            result = MockInference().generate({"next_slot": {"platform": platform}})
            self.assertTrue(validate_draft(result["draft"]))
            self.assertNotIn("\n\nDisanchor", result["draft"]["text"])

    def test_late_start_bounds_three_upcoming_slots_before_paid_work(self):
        self.now = datetime(2026, 10, 24, 20, tzinfo=UTC)
        config, store, _, runner, router = self.make(simulated=False)
        all_upcoming = [slot for slot in slots(config, self.now)
                        if self.now < datetime.fromisoformat(slot["scheduled_at"]) <= self.now + timedelta(hours=24)]
        self.assertEqual(len(all_upcoming), 3)
        result = router.run(self.now)
        self.assertEqual(result["status"], "AWAITING_REVIEW", result)
        research_context = runner.contexts[0][1]
        self.assertEqual(len(research_context["slots"]), 2)
        self.assertEqual([s["id"] for s in research_context["slots"]], ["x_01", "threads_01"])
        self.assertEqual(len(store.rows("SELECT * FROM post_drafts")), 2)

    def test_new_generated_copy_is_not_approved_by_seed_authorization(self):
        def mutate(stage, output, context):
            if stage == "writing":
                output["drafts"][0]["text"] = "When a project needs an old tool, what would you preserve first: the installer or its configuration? Disanchor is in development."
        _, store, _, _, router = self.make(simulated=False, authorize=True, mutate=mutate)
        result = router.run(self.now)
        self.assertEqual(result["status"], "AWAITING_REVIEW", result)
        row = store.one("SELECT * FROM post_drafts WHERE id='x_01'")
        self.assertTrue(row["text"].startswith("When a project needs"))
        self.assertEqual(row["state"], "DRAFT")
        self.assertIsNone(row["approved_hash"])
        self.assertFalse(store.rows("SELECT * FROM publishing_jobs"))

    def test_existing_exact_content_approval_is_preserved(self):
        def mutate(stage, output, context):
            if stage == "writing":
                output["drafts"][0]["text"] = "Which small configuration choice saves the most time when you start working on a different machine? Disanchor is in development."
        config, store, engine, _, router = self.make(simulated=False, authorize=True, mutate=mutate)
        before = engine.draft(slots(config, self.now)[0], self.now)
        self.assertEqual(before["state"], "APPROVED")
        result = router.run(self.now)
        self.assertEqual(result["status"], "AWAITING_REVIEW", result)
        after = store.one("SELECT * FROM post_drafts WHERE id=?", (before["id"],))
        self.assertEqual(after["text"], before["text"])
        self.assertEqual(after["approved_hash"], hashlib.sha256(before["text"].encode()).hexdigest())
        proposals = [r for r in store.records("content_ideas") if r["event_key"].startswith("route:")]
        self.assertEqual(len(proposals), 2)

    def test_unapproved_draft_adopts_proposal_but_still_needs_owner(self):
        def mutate(stage, output, context):
            if stage == "writing":
                output["drafts"][0]["text"] = "What belongs in a setup checklist besides applications, files and the settings you can remember? Disanchor is in development."
        config, store, engine, _, router = self.make(simulated=False, mutate=mutate)
        before = engine.draft(slots(config, self.now)[0], self.now)
        self.assertEqual(before["state"], "DRAFT")
        router.run(self.now)
        after = store.one("SELECT * FROM post_drafts WHERE id=?", (before["id"],))
        self.assertNotEqual(after["text"], before["text"])
        self.assertIsNone(after["approved_hash"])
        self.assertEqual(after["state"], "DRAFT")
        engine.approve(after["id"])
        self.assertEqual(store.one("SELECT state FROM post_drafts WHERE id=?", (after["id"],))["state"], "APPROVED")

    def test_repeated_run_and_recreated_router_never_repeat_calls(self):
        config, store, engine, runner, router = self.make()
        first = router.run(self.now)
        self.assertTrue(router.run(self.now)["reused"])
        replacement = FixtureRunner(config)
        resumed = StageRouter(engine, replacement).run(self.now)
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertTrue(resumed["reused"])
        self.assertEqual(len(runner.calls), 4)
        self.assertFalse(replacement.calls)
        self.assertEqual(len(store.rows("SELECT * FROM cost_ledger")), 4)

    def test_unknown_stage_is_reserved_and_never_retried_after_restart(self):
        config, store, engine, runner, router = self.make(simulated=False, fail_stage="planning")
        result = router.run(self.now)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([c[0] for c in runner.calls], ["research", "planning"])
        stage = store.one("SELECT * FROM routing_stage_runs WHERE stage='planning'")
        self.assertEqual(stage["state"], "UNKNOWN")
        self.assertEqual(stage["reserved_usd"], config.routing.planning.max_cost_per_call_usd)
        self.assertEqual(len(store.rows("SELECT * FROM cost_ledger WHERE classification='pending'")), 2)
        replacement = FixtureRunner(config, simulated=False)
        reused = StageRouter(engine, replacement).run(self.now)
        self.assertTrue(reused["reused"])
        self.assertEqual(reused["status"], "UNKNOWN")
        self.assertFalse(replacement.calls)

    def test_each_provider_call_has_durable_reservation_first(self):
        config, store, _, runner, router = self.make(simulated=False)
        original_run = runner.run
        def inspect_reservation(stage, route, context):
            row = store.one("SELECT * FROM routing_stage_runs WHERE stage=?", (stage,))
            self.assertIsNotNone(row)
            self.assertEqual(row["state"], "UNKNOWN")
            self.assertEqual(row["reserved_usd"], getattr(config.routing, stage).max_cost_per_call_usd)
            ledger = store.one("SELECT * FROM cost_ledger WHERE event_key=?", ("route-" + row["run_id"] + "-" + stage,))
            self.assertEqual(ledger["classification"], "pending")
            self.assertEqual(ledger["amount_usd"], row["reserved_usd"])
            self.assertFalse(store.db.in_transaction)
            return original_run(stage, route, context)
        with patch.object(runner, "run", side_effect=inspect_reservation):
            self.assertEqual(router.run(self.now)["status"], "AWAITING_REVIEW")

    def test_process_crash_keeps_unknown_reservation_and_blocks_new_route(self):
        config, store, engine, _, router = self.make(simulated=False, crash_stage="research")
        with self.assertRaises(KeyboardInterrupt):
            router.run(self.now)
        self.assertEqual(store.one("SELECT state FROM routing_stage_runs")["state"], "UNKNOWN")
        self.assertEqual(len(store.rows("SELECT * FROM cost_ledger")), 1)
        replacement = FixtureRunner(config, simulated=False)
        self.assertTrue(StageRouter(engine, replacement).run(self.now)["reused"])
        config.accounts.x_account = "default_x"
        blocked = StageRouter(engine, replacement).run(self.now)
        self.assertEqual(blocked["reason"], "unresolved_model_outcome")
        self.assertFalse(replacement.calls)

    def test_daily_pipeline_limit_counts_complete_runs(self):
        config, _, _, runner, router = self.make()
        self.assertEqual(router.run(self.now)["status"], "AWAITING_REVIEW")
        config.routing.expected_hermes_version = "fixture-version-2"
        self.assertEqual(router.run(self.now)["status"], "AWAITING_REVIEW")
        config.routing.expected_hermes_version = "fixture-version-3"
        self.assertEqual(router.run(self.now)["reason"], "daily_pipeline_limit")
        self.assertEqual(len(runner.calls), 8)

    def test_daily_stage_limit_blocks_another_whole_pipeline(self):
        config, _, _, runner, router = self.make()
        config.routing.max_stage_calls_per_day = 4
        self.assertEqual(router.run(self.now)["status"], "AWAITING_REVIEW")
        config.routing.expected_hermes_version = "fixture-version-2"
        self.assertEqual(router.run(self.now)["reason"], "daily_stage_call_limit")
        self.assertEqual(len(runner.calls), 4)

    def test_interrupted_proposal_save_does_not_report_complete_or_save_half(self):
        config, store, engine, runner, router = self.make(simulated=False)
        original_record = store.record
        writes = []
        def interrupted_record(table, key, *args, **kwargs):
            if table == "content_ideas" and key.startswith("route:"):
                writes.append(key)
                if len(writes) == 2:
                    raise RuntimeError("fixture disk write failure")
            return original_record(table, key, *args, **kwargs)
        with patch.object(store, "record", side_effect=interrupted_record):
            try:
                router.run(self.now)
            except RuntimeError:
                pass
        self.assertNotEqual(store.one("SELECT status FROM routing_runs")["status"], "AWAITING_REVIEW")
        self.assertFalse(store.rows("SELECT * FROM post_drafts"))
        self.assertFalse([row for row in store.records("content_ideas") if row["event_key"].startswith("route:")])
        replacement = FixtureRunner(config, simulated=False)
        recovered = StageRouter(engine, replacement).run(self.now)
        self.assertEqual(recovered["status"], "AWAITING_REVIEW")
        self.assertFalse(replacement.calls)
        self.assertEqual(len(runner.calls), 4)
        self.assertEqual(len(store.rows("SELECT * FROM post_drafts")), 2)
        self.assertEqual(len(store.rows("SELECT * FROM cost_ledger")), 4)
        self.assertTrue(all(row["account_fingerprint"] == engine.accounts.fingerprint()
                            for row in store.rows("SELECT * FROM post_drafts")))

    def test_disabled_routing_never_checks_or_calls_provider(self):
        config, store, _, runner, router = self.make(simulated=False)
        config.routing.enabled = False
        self.assertEqual(router.run(self.now)["status"], "disabled")
        self.assertFalse(runner.calls)
        self.assertFalse(runner.preflights)
        self.assertFalse(store.rows("SELECT * FROM cost_ledger"))

    def test_budget_and_billing_stop_before_any_provider_call(self):
        for change in (
            lambda c: setattr(c.budget, "billing_verified", False),
            lambda c: setattr(c.budget, "reconciled_at", iso(self.now - timedelta(hours=25))),
            lambda c: setattr(c.budget, "model_budget_usd", .1),
            lambda c: setattr(c.budget, "infrastructure_hourly_usd", .12),
        ):
            config, store, _, runner, router = self.make(simulated=False)
            change(config)
            result = router.run(self.now)
            self.assertEqual(result["reason"], "budget_or_billing_guard", result)
            self.assertFalse(runner.calls)
            self.assertFalse(runner.preflights)
            self.assertFalse(store.rows("SELECT * FROM cost_ledger"))

    def test_stage_budget_stops_even_when_overall_budget_remains(self):
        config, store, _, runner, router = self.make(simulated=False)
        store.db.execute("INSERT INTO routing_runs VALUES('past',? ,?,'2026-10-23','FAILED','worker','ctx','cfg')", (config.experiment.id, iso(self.now)))
        store.db.execute("INSERT INTO routing_stage_runs(id,run_id,stage,provider,model,state,created_at,reserved_usd) VALUES('past:research','past','research','openrouter','google/gemini-2.5-pro','FAILED',?,1.95)", (iso(self.now),))
        result = router.run(self.now)
        self.assertEqual(result["reason"], "stage_allowance_exhausted")
        self.assertEqual(result["stage"], "research")
        self.assertFalse(runner.calls)

    def test_unverified_runtime_caps_never_spawn_hermes(self):
        config, store, engine, _, _ = self.make(simulated=False)
        def forbidden(*args, **kwargs):
            raise AssertionError("unverified runtime must not spawn a process")
        result = StageRouter(engine, HermesStageRunner(config.routing, runner=forbidden)).run(self.now)
        self.assertEqual(result["reason"], "runtime_or_provider_cap_unverified")
        self.assertFalse(store.rows("SELECT * FROM cost_ledger"))

    def test_live_and_mock_runners_and_databases_cannot_mix(self):
        config, store, engine, _, _ = self.make()
        with self.assertRaises(ValueError):
            StageRouter(engine, FixtureRunner(config, simulated=False))
        wrong_engine = Engine(config, store, simulated=False)
        with self.assertRaises(GuardError):
            StageRouter(wrong_engine, FixtureRunner(config, simulated=False)).run(self.now)

    def test_paused_or_expired_experiment_cannot_start_pipeline(self):
        for expired in (False, True):
            _, store, _, runner, router = self.make(simulated=False)
            when = self.now + timedelta(hours=168) if expired else self.now
            if not expired:
                store.db.execute("UPDATE experiments SET status='PAUSED'")
            with self.assertRaises(GuardError):
                router.run(when)
            self.assertFalse(runner.calls)
            self.assertFalse(store.rows("SELECT * FROM cost_ledger"))

    def test_expiry_between_stages_stops_before_another_reservation(self):
        elapsed = [0]
        def mutate(stage, output, context):
            if stage == "research":
                elapsed[0] = 168 * 3600
        _, store, engine, runner, _ = self.make(simulated=False, mutate=mutate)
        result = StageRouter(engine, runner, clock=lambda: elapsed[0]).run(self.now)
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["stage"], "planning")
        self.assertEqual([row[0] for row in runner.calls], ["research"])
        self.assertEqual(len(store.rows("SELECT * FROM cost_ledger")), 1)
        self.assertEqual(len(store.rows("SELECT * FROM routing_stage_runs")), 1)

    def test_billing_expiry_between_stages_stops_new_paid_work(self):
        elapsed = [0]
        def mutate(stage, output, context):
            if stage == "research":
                elapsed[0] = 25 * 3600
        _, store, engine, runner, _ = self.make(simulated=False, mutate=mutate)
        result = StageRouter(engine, runner, clock=lambda: elapsed[0]).run(self.now)
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["stage"], "planning")
        self.assertEqual([row[0] for row in runner.calls], ["research"])
        self.assertEqual(len(store.rows("SELECT * FROM cost_ledger")), 1)

    def test_pause_or_new_unknown_charge_between_stages_stops(self):
        for change in ("pause", "charge"):
            _, store, engine, runner, _ = self.make(simulated=False)
            def mutate(stage, output, context):
                if stage != "research":
                    return
                if change == "pause":
                    store.db.execute("UPDATE experiments SET status='PAUSED'")
                else:
                    store.db.execute("INSERT INTO cost_ledger(event_key,observed_at,category,classification,amount_usd,source,note) VALUES('fixture-uncertain',?,'external','pending',NULL,'fixture','unknown incidental charge')", (iso(self.now),))
            runner.mutate = mutate
            result = StageRouter(engine, runner, clock=lambda: 0).run(self.now)
            self.assertEqual(result["status"], "STOPPED")
            self.assertEqual(result["stage"], "planning")
            self.assertEqual([row[0] for row in runner.calls], ["research"])
            self.assertEqual(len(store.rows("SELECT * FROM routing_stage_runs")), 1)

    def test_mock_does_not_open_live_evidence_files(self):
        config, _, _, _, router = self.make()
        config.routing.evidence_file = str(Path(self.tmp.name) / "must-not-exist.json")
        evidence = router.evidence()
        self.assertEqual([row["id"] for row in evidence], ["product-status"])

    def test_private_or_duplicate_evidence_is_rejected_before_provider(self):
        for extra in (
            [{"id": "private-note", "source": "reviewed note", "text": "contact person@example.com"}],
            [{"id": "product-status", "source": "duplicate", "text": "A copied ID is invalid."}],
        ):
            config, store, _, runner, router = self.make(simulated=False)
            evidence_file = Path(self.tmp.name) / f"evidence-{self.counter}.json"
            evidence_file.write_text(json.dumps(extra))
            config.routing.evidence_file = str(evidence_file)
            with self.assertRaises(ValueError):
                router.run(self.now)
            self.assertFalse(runner.calls)
            self.assertFalse(store.rows("SELECT * FROM cost_ledger"))

    def test_invented_evidence_ids_and_arbitrary_execution_are_rejected(self):
        context = {"evidence": [{"id": "known-source"}]}
        for stage, output in (
            ("research", {"findings": [{"claim": "Unsupported claim", "evidence_ids": ["invented"]}], "unknowns": [], "angle": "setup_overhead"}),
            ("planning", {"hypothesis": "H1", "hook": "question", "angle": "setup_overhead", "evidence_ids": ["invented"]}),
            ("execution", {"decision": "publish", "reason": "policy_review_required"}),
            ("execution", {"decision": "review", "reason": "policy_review_required", "command": "publish now"}),
        ):
            with self.subTest(stage=stage):
                with self.assertRaises(ValueError):
                    validate_stage(stage, output, context)

    def test_writing_policy_failure_prevents_execution_and_draft_storage(self):
        for bad in (
            "What is the hardest part of rebuilding your computer setup?",
            "Visit https://disanchor.com for the best workflow. Disanchor is in development.",
            "Which old program do you keep around? Sign up now. Disanchor is in development.",
        ):
            def mutate(stage, output, context):
                if stage == "writing":
                    output["drafts"][0]["text"] = bad
            _, store, _, runner, router = self.make(mutate=mutate)
            result = router.run(self.now)
            self.assertIn(result["status"], {"FAILED", "UNKNOWN"})
            self.assertEqual([c[0] for c in runner.calls], ["research", "planning", "writing"])
            self.assertFalse(store.rows("SELECT * FROM post_drafts"))

    def test_duplicate_or_wrong_slot_drafts_stop_before_execution(self):
        for violation in ("duplicate", "slot", "plan"):
            def mutate(stage, output, context):
                if stage != "writing":
                    return
                if violation == "duplicate":
                    output["drafts"][1]["text"] = output["drafts"][0]["text"]
                elif violation == "slot":
                    output["drafts"][0]["slot_id"] = "not-requested"
                else:
                    output["drafts"][0]["hypothesis"] = "H1"
            _, store, _, runner, router = self.make(mutate=mutate)
            result = router.run(self.now)
            self.assertIn(result["status"], {"FAILED", "UNKNOWN"})
            self.assertEqual([c[0] for c in runner.calls], ["research", "planning", "writing"])
            self.assertFalse(store.rows("SELECT * FROM post_drafts"))

    def test_execution_rejection_never_stores_publishable_drafts(self):
        def mutate(stage, output, context):
            if stage == "execution":
                output.update(decision="reject", reason="insufficient_evidence")
        _, store, _, _, router = self.make(mutate=mutate)
        self.assertEqual(router.run(self.now)["status"], "REJECTED")
        self.assertFalse(store.rows("SELECT * FROM post_drafts"))
        self.assertFalse(store.rows("SELECT * FROM publishing_jobs"))

    def test_daily_keeps_generated_drafts_unapproved_even_with_owner_authorization(self):
        config, store, engine, runner, router = self.make(simulated=False, authorize=True)
        config.publishing.auto_publish = True
        config.publishing.dry_run = False
        config.publishing.account_remaining_quota = 10
        engine.authorize(self.now, reviewed_policy=True, approve_library=True, account_user="fixture-owner")
        class RecordingPublisher:
            simulated = False
            def __init__(self):
                self.calls = []
            def quota(self):
                return {"remaining": 10}
            def schedule(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError("A generated DRAFT must never reach a publisher")
        publisher = RecordingPublisher()
        engine.publisher = publisher
        with patch("gtm.routing.StageRouter", return_value=router):
            result = engine.daily(self.now)
        self.assertEqual(result["model_generation"]["status"], "AWAITING_REVIEW")
        self.assertEqual(result["outcomes"], {"x_01": "blocked_by_guard", "threads_01": "blocked_by_guard"})
        self.assertEqual(len(runner.calls), 4)
        self.assertTrue(all(row["state"] == "DRAFT" and row["approved_hash"] is None for row in store.rows("SELECT * FROM post_drafts")))
        self.assertFalse(publisher.calls)
        self.assertFalse(store.rows("SELECT * FROM publishing_jobs"))

    def test_native_adapter_full_pipeline_prompts_fit_with_recent_metrics(self):
        config, store, engine, _, _ = self.make(simulated=False)
        config.routing.expected_hermes_version = FixtureProcess.version
        config.routing.runtime_contract_verified = True
        config.routing.billing_cap_verified = True
        config.routing.output_cap_verified = True
        # Exercise the normal maximum history carried into the writing stage.
        for slot in slots(config, self.now)[2:6]:
            engine.draft(slot, self.now)
        for index in range(3):
            store.record("post_metrics_snapshots", f"fixture-metric-{index}", self.now, "MANUAL platform insights",
                         {"post_id": f"old_post_{index}", "observed_at": iso(self.now),
                          "metrics": {"views": 100 + index, "impressions": None, "likes": 2, "replies": 1,
                                      "reposts": 0, "quotes": 0, "link_clicks": None, "followers": None}})
        process = PipelineFixtureProcess(config)
        native = HermesStageRunner(config.routing, runner=process)
        result = StageRouter(engine, native, clock=lambda: 0).run(self.now)
        self.assertEqual(result["status"], "AWAITING_REVIEW", result)
        self.assertEqual([stage for stage, _, _ in process.paid_calls], list(STAGES))
        for stage, argv, prompt in process.paid_calls:
            route = getattr(config.routing, stage)
            self.assertLessEqual(len(prompt.encode()), route.max_input_tokens, stage)
            self.assertEqual(argv[argv.index("--model") + 1], route.model)
        research = json.loads(process.paid_calls[0][2].split("UNTRUSTED_INPUT_JSON:\n", 1)[1])
        writing = json.loads(process.paid_calls[2][2].split("UNTRUSTED_INPUT_JSON:\n", 1)[1])
        execution = json.loads(process.paid_calls[3][2].split("UNTRUSTED_INPUT_JSON:\n", 1)[1])
        self.assertEqual(len(research["evidence"]), 4)
        self.assertEqual(len(execution["evidence"]), 4)
        self.assertEqual(len(writing["previous_texts"]), 4)
        self.assertEqual(len(writing["accounts"]), 2)
        self.assertEqual(execution["plan"]["hypothesis"], "H2")
        self.assertEqual(len(store.rows("SELECT * FROM routing_stage_runs WHERE state='SUCCEEDED'")), 4)


class FixtureProcess:
    version = "Hermes Agent fixture native routing"
    help = "--query-file --format --quiet --provider --model --safe-mode --toolsets --max-turns"

    def __init__(self):
        self.calls = []
        self.extra = []
        self.failure = None
        self.output = {"findings": [], "unknowns": ["No external observations supplied."], "angle": "setup_overhead"}
        self.tokens = {"input": 2000, "output": 150}

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, self.version + "\n", "")
        if argv[1:] == ["chat", "--help"]:
            return subprocess.CompletedProcess(argv, 0, self.help, "")
        if self.failure:
            raise self.failure
        records = self.extra + [{"type": "result", "exit_code": 0, "tokens": self.tokens, "text": json.dumps(self.output)}]
        return subprocess.CompletedProcess(argv, 0, "\n".join(json.dumps(r) for r in records), "")


class PipelineFixtureProcess(FixtureProcess):
    """Exercise real prompt construction/stream parsing with local stage outputs."""
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.generator = MockStageRunner(config)
        self.paid_calls = []

    def __call__(self, argv, **kwargs):
        if "input" not in kwargs:
            return super().__call__(argv, **kwargs)
        self.calls.append((argv, kwargs))
        stage = STAGES[len(self.paid_calls)]
        prompt = kwargs["input"]
        self.paid_calls.append((stage, argv, prompt))
        context = json.loads(prompt.split("UNTRUSTED_INPUT_JSON:\n", 1)[1])
        response = self.generator.run(stage, getattr(self.config.routing, stage), context)["output"]
        text = json.dumps(response)
        terminal = {"type": "result", "exit_code": 0, "session_id": "fixture-only",
                    "tokens": {"input": len(prompt.encode()), "output": max(1, len(text) // 4)}, "text": text}
        return subprocess.CompletedProcess(argv, 0, json.dumps(terminal) + "\n", "")


class NativeRoutingContractTests(unittest.TestCase):
    def make(self):
        config = Config()
        config.routing.enabled = True
        process = FixtureProcess()
        config.routing.expected_hermes_version = process.version
        config.routing.runtime_contract_verified = True
        config.routing.billing_cap_verified = True
        config.routing.output_cap_verified = True
        return config, process, HermesStageRunner(config.routing, runner=process)

    def test_native_call_has_pinned_route_stdin_and_no_execution_tools(self):
        config, process, runner = self.make()
        result = runner.run("research", config.routing.research, {"evidence": [], "slots": []})
        self.assertEqual(result["status"], "succeeded", result)
        self.assertIsNone(result["cost_usd"])
        argv, kwargs = process.calls[-1]
        self.assertEqual(argv[argv.index("--provider") + 1], "openrouter")
        self.assertEqual(argv[argv.index("--model") + 1], "google/gemini-2.5-pro")
        self.assertEqual(argv[argv.index("--toolsets") + 1], "clarify")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertIn("--safe-mode", argv)
        self.assertNotIn("--yolo", argv)
        self.assertNotIn("shell", kwargs)
        self.assertIn("You have no web-search access", kwargs["input"])
        self.assertIn("UNTRUSTED_INPUT_JSON", kwargs["input"])

    def test_model_stage_environment_has_only_selected_provider_not_publishing_credentials(self):
        config, process, runner = self.make()
        environment = {"OPENROUTER_API_KEY": "offline-provider", "UPLOAD_POST_API_KEY": "offline-social",
                       "TELEGRAM_BOT_TOKEN": "offline-chat", "CLONE_APP_KEY": "offline-clone",
                       "GA4_ACCESS_TOKEN": "offline-ga4", "PYTHONPATH": "/unexpected/import",
                       "HERMES_PROFILE": "unexpected-profile"}
        with patch.dict("os.environ", environment, clear=True):
            result = runner.run("research", config.routing.research, {"evidence": [], "slots": []})
        self.assertEqual(result["status"], "succeeded")
        for _, kwargs in process.calls:
            self.assertIn("env", kwargs)
            for key in set(environment) - {"OPENROUTER_API_KEY"}:
                self.assertNotIn(key, kwargs["env"])
            self.assertEqual("OPENROUTER_API_KEY" in kwargs["env"], "input" in kwargs)

    def test_tool_request_and_over_cap_tokens_are_rejected(self):
        for violation in ("tools", "tokens", "missingusage"):
            config, process, runner = self.make()
            if violation == "tools":
                process.extra = [{"type": "tool_use", "name": "terminal", "input": {"command": "publish"}}]
            elif violation == "tokens":
                process.tokens["output"] = config.routing.research.max_output_tokens + 1
            else:
                process.tokens = {}
            result = runner.run("research", config.routing.research, {"evidence": [], "slots": []})
            self.assertEqual(result["status"], "rejected", result)

    def test_input_bound_is_checked_before_paid_invocation(self):
        config, process, runner = self.make()
        config.routing.research.max_input_tokens = 50
        result = runner.run("research", config.routing.research, {"evidence": [], "slots": []})
        self.assertEqual(result["reason"], "input_bound_exceeded")
        self.assertTrue(all("input" not in kwargs for _, kwargs in process.calls))

    def test_provider_timeout_is_unknown_without_automatic_retry(self):
        config, process, runner = self.make()
        process.failure = subprocess.TimeoutExpired("hermes", 90, stderr="private-token")
        result = runner.run("research", config.routing.research, {"evidence": [], "slots": []})
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(sum("input" in kwargs for _, kwargs in process.calls), 1)
        self.assertNotIn("private-token", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
