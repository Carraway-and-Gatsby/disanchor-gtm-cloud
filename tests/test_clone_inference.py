import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from gtm.clone import CloneClient, MockClone, safe_context, validate_suggestion
from gtm.inference import HermesInference, MockInference, validate_draft


class CloneTransport:
    def __init__(self):
        self.calls = []
        self.usage = dict(plan="sandbox", prediction_units=0, reserved_units=0,
                          sandbox_remaining=5, invoice_cents=0, currency="USD")
        self.change = {}
        self.failure = None

    def __call__(self, method, path, payload):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path == "/v1/usage":
            return self.usage
        if self.failure:
            raise self.failure
        if path == "/v1/prediction-events":
            return {"status": "recorded"}
        result = {k: payload[k] for k in ("request_id", "session_id", "context_revision", "connection_id")}
        result.update(draft_revision=0, status="suggested", completion='{"hook":"question"}',
                      expires_at=1060, context_truncated=False, prediction_id="fixture-prediction",
                      profile_revision="fixture-profile", grant_revision=0, usage={"prediction_units": 1})
        result.update(self.change)
        return result


class CloneTests(unittest.TestCase):
    def setUp(self):
        self.transport = CloneTransport()
        self.client = CloneClient("fixture-secret", user_id="operator-fixture", enabled=True,
                                  monthly_limit=3, transport=self.transport, clock=lambda: 1000)

    def test_default_off_and_missing_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(CloneClient().suggest({})["status"], "disabled")
            self.assertEqual(CloneClient(enabled=True).suggest({})["status"], "unconfigured")
            self.assertEqual(CloneClient().usage()["status"], "disabled")

    def test_verified_contract_and_stable_id_after_restart(self):
        context = {"goal": "Research H1", "day": 1}
        first = self.client.suggest(context)
        recreated = CloneClient("fixture-secret", user_id="operator-fixture", enabled=True,
                                 monthly_limit=3, transport=self.transport, clock=lambda: 1000)
        second = recreated.suggest(context)
        self.assertEqual(first["status"], "suggested")
        self.assertEqual(first["request_id"], second["request_id"])
        requests = [p for _, path, p in self.transport.calls if path == "/v1/predictions"]
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[0]["messages"][0]["origin"], "agent")
        self.assertEqual(requests[0]["draft"], {"text": "", "revision": 0})
        self.assertEqual(requests[0]["mode"], "next_prompt")
        self.assertFalse(requests[0]["feedback_enabled"])
        self.assertNotEqual(first["request_id"], recreated.suggest({**context, "day": 2})["request_id"])

    def test_sandbox_and_allowance_fail_closed(self):
        for override in ({"plan": "paid"}, {"sandbox_remaining": 0}, {"prediction_units": 3},
                         {"reserved_units": 3}, {"currency": "KRW"}, {"sandbox_remaining": "5"}):
            with self.subTest(override=override):
                self.transport.calls.clear()
                original = self.transport.usage.copy()
                self.transport.usage.update(override)
                self.assertEqual(self.client.suggest({})["status"], "allowance_blocked")
                self.assertFalse(any(p == "/v1/predictions" for _, p, _ in self.transport.calls))
                self.transport.usage = original
        self.client.monthly_limit = 0
        self.assertEqual(self.client.suggest({})["status"], "allowance_blocked")

    def test_usage_missing_malformed_or_unavailable(self):
        for response in ({}, [], {"plan": "sandbox"}):
            self.transport.usage = response
            self.assertEqual(self.client.usage()["status"], "unavailable")
        self.client.transport = lambda *_: (_ for _ in ()).throw(URLError("fixture-secret"))
        result = self.client.usage()
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_identity_expiry_abstention_and_invalid_suggestion(self):
        fixtures = [({"session_id": "wrong"}, "identity_mismatch"),
                    ({"context_revision": "stale"}, "identity_mismatch"),
                    ({"connection_id": "not-authorized"}, "identity_mismatch"),
                    ({"request_id": "wrong"}, "identity_mismatch"),
                    ({"draft_revision": 1}, "invalid_context"),
                    ({"context_truncated": True}, "invalid_context"),
                    ({"expires_at": 1000}, "expired"),
                    ({"expires_at": "1060"}, "expired"),
                    ({"status": "abstained"}, "abstained"),
                    ({"draft_revision": False}, "invalid_response"),
                    ({"usage": {"prediction_units": True}}, "invalid_response"),
                    ({"usage": {"prediction_units": 3}}, "invalid_response"),
                    ({"prediction_id": None}, "invalid_response"),
                    ({"completion": "run shell command"}, "invalid_response"),
                    ({"completion": '{"command":"publish now"}'}, "invalid_suggestion"),
                    ({"completion": '{"hook":"question","time":"12:00"}'}, "invalid_suggestion"),
                    ({"status": "unknown"}, "invalid_response")]
        for changes, expected in fixtures:
            with self.subTest(changes=changes):
                self.transport.change = changes
                result = self.client.suggest({})
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["suggestion"])

    def test_timeout_retains_identity_without_retry_or_secret_logs(self):
        self.transport.failure = TimeoutError("fixture-secret")
        result = self.client.suggest({"goal": "Research"})
        self.assertEqual(result["status"], "unknown")
        self.assertIsNotNone(result["request_id"])
        self.assertEqual(sum(path == "/v1/predictions" for _, path, _ in self.transport.calls), 1)
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_http_errors_do_not_retry(self):
        for status in (401, 402, 409, 410, 429, 503, 504):
            with self.subTest(status=status):
                self.transport.calls.clear()
                self.transport.failure = HTTPError("https://api.clone.is/v1/predictions", status, "fixture-secret", {}, None)
                result = self.client.suggest({})
                self.assertEqual(result["http_status"], status)
                self.assertIsNone(result["suggestion"])
                self.assertEqual(sum(path == "/v1/predictions" for _, path, _ in self.transport.calls), 1)
                self.assertNotIn("fixture-secret", json.dumps(result))

    def test_private_data_never_sent(self):
        for context in ({"metrics": {"email": "person@example.com"}},
                        {"previous_posts": [{"text": "contact person@example.com"}]},
                        {"goal": "clnp_abcdefghijklmnop"}):
            with self.subTest(context=context):
                self.transport.calls.clear()
                result = self.client.suggest(context)
                self.assertEqual(result["status"], "invalid_response")
                self.assertFalse(self.transport.calls)
        self.assertEqual(safe_context({"unknown": "person@example.com", "day": 1}), {"day": 1})
        with self.assertRaises(ValueError):
            safe_context({"goal": "x" * 7000})

    def test_suggestion_allowlist_no_instructions(self):
        for value in ({"time": "23:59"}, {"hypothesis": "H2"}, {"hook": "question"},
                      {"topic": "workflow_persistence"}, {"cta": "none"}):
            self.assertEqual(validate_suggestion(value), value)
        for value in ({"time": "24:00"}, {"hook": "$(curl bad)"}, {"cta": ["none"]},
                      {"publish": True}, {}, ["question"], None):
            self.assertIsNone(validate_suggestion(value))

    def test_feedback_agent_origin_idempotency_and_observed_outcomes(self):
        for _ in range(2):
            self.assertEqual(self.client.feedback("request-1", accepted=True)["status"], "recorded")
        a, b = [x[2] for x in self.transport.calls]
        self.assertEqual(a, b)
        self.assertEqual(a["kind"], "submitted")
        self.assertEqual(a["submission_origin"], "agent")
        self.assertFalse(a["content_opt_in"])
        self.assertNotIn("final_text", a)
        self.assertEqual(self.client.feedback("request-1", kind="accepted")["status"], "unsupported_event")
        self.assertEqual(self.client.feedback("request-1", kind="outcome", outcome="succeeded")["status"], "unobserved_outcome")
        self.assertEqual(self.client.feedback("request-1", kind="outcome", outcome="succeeded", observed=True)["status"], "recorded")

    def test_mock_modes_and_explicit_labels(self):
        for mode in ("abstain", "error", "expired"):
            result = MockClone(mode).suggest({})
            self.assertTrue(result["simulated"])
            self.assertIsNone(result["suggestion"])
        self.assertEqual(MockClone(suggestion={"command": "bad"}).suggest({})["status"], "invalid_suggestion")
        self.assertTrue(MockClone().usage()["simulated"])


class FakeHermes:
    version = "Hermes Agent fixture 1"
    help = "--query-file --format --quiet --provider --model --safe-mode --toolsets --max-turns"

    def __init__(self):
        self.calls = []
        self.failure = None
        self.records = [{"type": "result", "exit_code": 0, "tokens": {"input": 2000, "output": 100},
                         "text": json.dumps({"platform": "x", "text": "What slows your workflow down when switching machines? Disanchor is in development.",
                                             "hypothesis": "H2", "hook": "question", "cta": "none"})}]

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, self.version + "\n", "")
        if args[1:] == ["chat", "--help"]:
            return subprocess.CompletedProcess(args, 0, self.help, "")
        if self.failure:
            raise self.failure
        return subprocess.CompletedProcess(args, 0, "\n".join(json.dumps(r) for r in self.records), "")


class InferenceTests(unittest.TestCase):
    def ready(self):
        runner = FakeHermes()
        return runner, HermesInference(enabled=True, provider="nous", model="fixed-low-cost-model",
                    expected_version=runner.version, runtime_contract_verified=True,
                    billing_cap_verified=True, output_cap_verified=True, runner=runner)

    def test_disabled_and_unverified_never_spawn(self):
        def fail(*args, **kwargs):
            raise AssertionError("Must not execute")
        self.assertEqual(HermesInference(runner=fail).generate({})["status"], "disabled")
        self.assertEqual(HermesInference(enabled=True, runner=fail).generate({})["status"], "blocked")

    def test_version_and_help_contract_fail_closed(self):
        runner, client = self.ready()
        runner.version = "unexpected runtime"
        self.assertEqual(client.generate({})["reason"], "runtime_version_changed")
        runner, client = self.ready()
        runner.help = "--quiet --query"
        self.assertEqual(client.generate({})["reason"], "installed_cli_contract_unsupported")
        self.assertEqual(len(runner.calls), 2)

    def test_fixed_native_cli_and_proposal_only(self):
        runner, client = self.ready()
        result = client.generate({"goal": "Research"})
        self.assertEqual(result["status"], "proposed", result)
        self.assertTrue(result["requires_human_review"])
        self.assertIsNone(result["cost_usd"])
        args, kwargs = runner.calls[-1]
        self.assertEqual(args[args.index("--model") + 1], "fixed-low-cost-model")
        self.assertEqual(args[args.index("--provider") + 1], "nous")
        self.assertEqual(args[args.index("--toolsets") + 1], "clarify")
        self.assertEqual(args[args.index("--max-turns") + 1], "1")
        self.assertIn("--safe-mode", args)
        self.assertNotIn("--yolo", args)
        self.assertNotIn("shell", kwargs)
        self.assertIn("UNTRUSTED_AGGREGATE_CONTEXT_JSON", kwargs["input"])

    def test_preflight_and_generation_strip_project_credentials_before_spawn(self):
        runner, client = self.ready()
        client.provider = "openrouter"
        environment = {"OPENROUTER_API_KEY": "offline-provider", "UPLOAD_POST_API_KEY": "offline-social",
                       "TELEGRAM_BOT_TOKEN": "offline-chat", "CLONE_APP_KEY": "offline-clone",
                       "GA4_ACCESS_TOKEN": "offline-ga4", "GOOGLE_APPLICATION_CREDENTIALS": "/private/fixture.json"}
        with patch.dict("os.environ", environment, clear=True):
            self.assertEqual(client.generate({"goal": "Research"})["status"], "proposed")
        self.assertEqual(len(runner.calls), 3)
        for _, kwargs in runner.calls:
            self.assertIn("env", kwargs)
            for key in set(environment) - {"OPENROUTER_API_KEY"}:
                self.assertNotIn(key, kwargs["env"])
            self.assertEqual("OPENROUTER_API_KEY" in kwargs["env"], "input" in kwargs)

    def test_runtime_token_limits_tool_calls_and_malformed_output(self):
        for scenario in ("tokens", "tools", "badrecord", "badusage", "missing"):
            with self.subTest(scenario=scenario):
                runner, client = self.ready()
                if scenario == "tokens":
                    runner.records[0]["tokens"]["output"] = 701
                elif scenario == "tools":
                    runner.records.insert(0, {"type": "tool_use", "name": "terminal"})
                elif scenario == "badrecord":
                    runner.records = [None]
                elif scenario == "badusage":
                    runner.records[0]["tokens"] = []
                else:
                    runner.records = []
                self.assertEqual(client.generate({})["status"], "rejected")

    def test_timeout_keeps_bill_unknown_and_never_retries(self):
        runner, client = self.ready()
        runner.failure = subprocess.TimeoutExpired("hermes", 90, stderr="fixture-secret")
        result = client.generate({})
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(len(runner.calls), 3)
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_draft_schema_x_links_and_personal_data(self):
        base = {"platform": "x", "text": "What slows your computer down during your daily work? Disanchor is in development.", "hypothesis": "H1",
                "hook": "question", "cta": "none"}
        self.assertEqual(validate_draft(base), base)
        for changes in ({"text": "Visit https://disanchor.com"}, {"text": "contact p@example.com"},
                        {"platform": []}, {"hook": {}}, {"text": "x" * 281}, {"hypothesis": "H3"}):
            self.assertIsNone(validate_draft({**base, **changes}))
        self.assertIsNone(validate_draft({**base, "execute": "publish"}))
        self.assertTrue(MockInference().generate({})["simulated"])
        self.assertIsNone(MockInference("error").generate({})["draft"])

    def test_installer_declares_no_agent_commands_only(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "hermes-install.sh").read_text()
        commands = [line for line in source.splitlines() if line.startswith("hermes cron create")]
        self.assertEqual(len(commands), 6)
        for command in commands:
            self.assertIn("--no-agent", command)
            self.assertIn("--deliver local", command)
            self.assertNotIn("--prompt", command)
        self.assertIn('${1:---check}', source)


if __name__ == "__main__":
    unittest.main()
