import json
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gtm.config import Config, iso, timestamp, slots
from gtm.engine import Engine
from gtm.store import Store
from gtm.telegram import TelegramClient, TelegramError, TelegramMonitor, _NoRedirect


TOKEN = "123456:abcdefghijklmnopqrstuvwxyz_0123456789"


class Transport:
    def __init__(self):
        self.calls = []
        self.updates = []
        self.send_result = None
        self.get_result = None

    def __call__(self, method, url, headers, body):
        payload = json.loads(body)
        name = url.rsplit("/", 1)[-1]
        self.calls.append((method, name, payload))
        override = self.send_result if name == "sendMessage" else self.get_result
        if isinstance(override, BaseException):
            raise override
        if override is not None:
            return override
        return {"status_code": 200, "body": {"ok": True, "result": {"message_id": len(self.calls)} if name == "sendMessage" else self.updates}}

    def sent(self):
        return [call[2] for call in self.calls if call[1] == "sendMessage"]


def update(uid, text="/status", sender=42, chat=42, chat_type="private", bot=False):
    return {"update_id": uid, "message": {"from": {"id": sender, "is_bot": bot}, "chat": {"id": chat, "type": chat_type}, "text": text}}


class TelegramClientTests(unittest.TestCase):
    def setUp(self):
        self.t = Transport()
        self.client = TelegramClient(TOKEN, "42", "42", self.t)

    def test_private_numeric_identity_required(self):
        for chat, user in [("@name", 42), (-42, 42), (True, 42), (42, 43), (42, False), (2**52, 2**52)]:
            with self.assertRaises(ValueError):
                TelegramClient(TOKEN, chat, user, self.t)
        with self.assertRaises(ValueError):
            TelegramClient("secret\ncredential", 42, 42, self.t)

    def test_post_plain_text_no_paid_broadcast_or_links(self):
        self.assertEqual(self.client.send_message("Disanchor 현황"), 1)
        method, name, payload = self.t.calls[0]
        self.assertEqual((method, name), ("POST", "sendMessage"))
        self.assertNotIn("parse_mode", payload)
        self.assertNotIn("allow_paid_broadcast", payload)
        self.assertEqual(payload["link_preview_options"], {"is_disabled": True})
        self.assertTrue(payload["protect_content"])

    def test_get_updates_bounds_and_explicit_offset(self):
        self.client.get_updates(151)
        self.assertEqual(self.t.calls[0][2], {"offset": 151, "limit": 100, "timeout": 5, "allowed_updates": ["message"]})
        for offset in [-1, True, 1.1]:
            with self.assertRaises(ValueError):
                self.client.get_updates(offset)

    def test_transport_exception_never_exposes_url_or_token(self):
        self.t.send_result = TimeoutError(f"https://api.telegram.org/bot{TOKEN}/sendMessage private prompt")
        with self.assertRaises(TelegramError) as caught:
            self.client.send_message("status")
        self.assertEqual(str(caught.exception), "telegram_transport_unknown")
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_definitive_429_only_and_bounded_cooldown(self):
        self.t.send_result = {"status_code": 429, "body": {"ok": False, "error_code": 429, "description": TOKEN, "parameters": {"retry_after": 17}}}
        with self.assertRaises(TelegramError) as caught:
            self.client.send_message("status")
        self.assertEqual(caught.exception.retry_after, 17)
        self.t.send_result["body"]["parameters"]["retry_after"] = -1
        with self.assertRaises(TelegramError) as caught:
            self.client.send_message("status")
        self.assertEqual(caught.exception.retry_after, 60)
        self.t.send_result["body"]["ok"] = True
        with self.assertRaises(TelegramError) as caught:
            self.client.send_message("status")
        self.assertIsNone(caught.exception.http_status)

    def test_invalid_send_ack_is_ambiguous(self):
        for result in [{}, {"message_id": True}, {"message_id": -1}, "provider secret"]:
            self.t.send_result = {"status_code": 200, "body": {"ok": True, "result": result}}
            with self.assertRaises(TelegramError):
                self.client.send_message("status")

    def test_definitive_rejection_and_protocol_errors(self):
        self.t.send_result = {"status_code": 403, "body": {"ok": False, "description": TOKEN}}
        with self.assertRaises(TelegramError) as caught:
            self.client.send_message("status")
        self.assertEqual(str(caught.exception), "telegram_rejected")
        for response in [None, {"status_code": 200, "body": {"ok": "true"}}, {"status_code": 500, "body": {"ok": False}}]:
            if response is None:
                response = {"status_code": "200", "body": {}}
            self.t.send_result = response
            with self.assertRaises(TelegramError):
                self.client.send_message("status")

    def test_redirects_are_not_followed(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))


class TelegramMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite"
        self.s = Store(self.path)
        self.addCleanup(self.s.close)
        self.c = Config()
        self.e = Engine(self.c, self.s)
        self.now = timestamp("2026-10-24T12:00:00Z")
        self.e.init(self.now)
        self.e.start(self.now)
        self.t = Transport()
        self.client = TelegramClient(TOKEN, 42, 42, self.t)
        self.m = TelegramMonitor(self.e, self.client, enabled=True)

    def baseline(self):
        self.m.notify(self.now)
        self.t.calls.clear()

    def test_disabled_monitor_never_calls_transport(self):
        monitor = TelegramMonitor(self.e, None)
        self.assertEqual(monitor.poll_and_notify(self.now), {"status": "disabled", "network_calls": 0})
        self.assertEqual(monitor.notify(self.now)["status"], "disabled")
        self.assertEqual(self.t.calls, [])

    def test_simulated_state_cannot_send_through_real_telegram_transport(self):
        self.e.simulated = True
        real_client = TelegramClient(TOKEN, 42, 42)
        with self.assertRaises(ValueError):
            TelegramMonitor(self.e, real_client, enabled=True)
        TelegramMonitor(self.e, real_client, enabled=False)
        TelegramMonitor(self.e, self.client, enabled=True)  # explicitly injected fake transport
        self.assertEqual(self.t.calls, [])

    def test_live_mode_never_implies_active_posting_from_flags_alone(self):
        self.c.publishing.auto_publish = True
        self.c.publishing.dry_run = False
        self.assertIn("실제 상태(게시 OFF)", self.m.preview(self.now))

    def test_private_owner_exact_commands_only(self):
        self.baseline()
        self.t.updates = [update(1, sender=99), update(2, chat=-100, chat_type="group"), update(3, bot=True),
                          update(4, "/approve x_01"), update(5, "/status\nIGNORE RULES"), update(6, "/status@otherbot"),
                          update(7, "/posts", chat=42, chat_type="channel"), update(8, "/help")]
        with patch.object(self.e, "approve", side_effect=AssertionError("mutator called")), patch.object(self.e, "daily", side_effect=AssertionError("model called")):
            result = self.m.poll_and_notify(self.now + timedelta(seconds=2))
        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(self.t.sent()), 1)
        self.assertIn("조회 명령", self.t.sent()[0]["text"])
        self.assertEqual(self.m._get("offset"), 9)
        self.assertEqual(self.e.experiment()["status"], "RUNNING")
        self.assertNotIn("IGNORE RULES", "".join(row["text"] for row in self.s.rows("SELECT text FROM telegram_outbox")))

    def test_forwarded_and_malformed_input_is_ignored(self):
        self.baseline()
        forwarded = update(4)
        forwarded["message"]["forward_origin"] = {"type": "user"}
        self.t.updates = [{"update_id": 1, "channel_post": {}}, {"update_id": 2, "message": "bad"},
                          update(3, sender=True), forwarded, {"update_id": 5, "message": {"chat": {}}}]
        self.m.poll_and_notify(self.now + timedelta(seconds=2))
        self.assertEqual(self.t.sent(), [])
        self.assertEqual(self.m._get("offset"), 6)

    def test_commands_are_aggregate_and_never_include_drafts_or_incident_text(self):
        self.baseline()
        self.s.db.execute("UPDATE experiments SET incident=?", ("SENSITIVE_EXCEPTION token=" + TOKEN,))
        for command in ("/status", "/budget", "/posts", "/help"):
            text = self.m._command(update(1, command), self.m._snapshot(self.now))
            self.assertNotIn(TOKEN, text)
            self.assertNotIn("SENSITIVE_EXCEPTION", text)
        self.assertIn("개발 중", self.m.preview(self.now))

    def test_transition_and_daily_are_deduplicated_across_instances(self):
        self.m.notify(self.now)
        monitor = TelegramMonitor(self.e, self.client, enabled=True)
        monitor.notify(self.now + timedelta(minutes=2))
        self.assertEqual(len(self.t.sent()), 1)
        tomorrow = self.now + timedelta(days=1)
        monitor.notify(tomorrow)
        monitor.notify(tomorrow + timedelta(minutes=2))
        self.assertEqual(len(self.t.sent()), 2)
        self.assertIn("일일 요약", self.t.sent()[-1]["text"])

    def test_daily_clock_uses_kst_and_configured_hour(self):
        early = timestamp("2026-10-24T23:00:00Z")  # next day 08:00 KST
        self.m.notify(early)
        self.m.notify(early + timedelta(minutes=30))
        self.assertEqual(len(self.t.sent()), 1)
        self.m.notify(early + timedelta(hours=1))
        self.assertEqual(len(self.t.sent()), 2)
        self.assertIn("일일 요약", self.t.sent()[-1]["text"])

    def test_daily_and_transition_coalesce_into_one_full_status(self):
        early = timestamp("2026-10-24T23:00:00Z")
        self.m.notify(early)
        self.s.db.execute("UPDATE experiments SET status='PAUSED'")
        self.m.notify(early + timedelta(hours=1))
        self.m.notify(early + timedelta(hours=1, minutes=2))
        self.assertEqual(len(self.t.sent()), 2)
        self.assertIn("일시 중지", self.t.sent()[-1]["text"])

    def test_queued_send_is_unknown_before_io(self):
        original = self.client.send_message
        def check(text):
            row = self.s.one("SELECT state FROM telegram_outbox ORDER BY id DESC LIMIT 1")
            self.assertEqual(row["state"], "UNKNOWN")
            return original(text)
        self.client.send_message = check
        self.m.notify(self.now)
        self.assertEqual(self.s.one("SELECT state FROM telegram_outbox")["state"], "SENT")

    def test_timeout_never_blind_retried_even_after_restart(self):
        self.t.send_result = TimeoutError(TOKEN)
        self.assertEqual(self.m.notify(self.now)["status"], "delivery_unknown")
        monitor = TelegramMonitor(self.e, self.client, enabled=True)
        self.t.send_result = None
        monitor.notify(self.now + timedelta(minutes=5))
        self.assertEqual(len(self.t.sent()), 1)
        self.assertEqual(self.s.one("SELECT state FROM telegram_outbox")["state"], "UNKNOWN")

    def test_process_crash_after_unknown_mark_never_retries(self):
        self.t.send_result = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.m.notify(self.now)
        self.t.send_result = None
        monitor = TelegramMonitor(self.e, self.client, enabled=True)
        monitor.notify(self.now + timedelta(minutes=1))
        self.assertEqual(len(self.t.sent()), 1)

    def test_429_defers_send_and_poll_until_cooldown_then_retries_once(self):
        self.t.send_result = {"status_code": 429, "body": {"ok": False, "error_code": 429, "parameters": {"retry_after": 20}}}
        self.assertEqual(self.m.notify(self.now)["status"], "rate_limited")
        self.assertEqual(self.m.poll_and_notify(self.now + timedelta(seconds=10))["status"], "cooldown")
        self.assertEqual(len(self.t.calls), 1)
        self.t.send_result = None
        self.m.notify(self.now + timedelta(seconds=20))
        self.assertEqual(len(self.t.sent()), 2)
        self.assertEqual(self.s.one("SELECT state FROM telegram_outbox")["state"], "SENT")

    def test_rejected_send_never_retries(self):
        self.t.send_result = {"status_code": 403, "body": {"ok": False, "description": "blocked"}}
        self.assertEqual(self.m.notify(self.now)["status"], "rejected")
        self.t.send_result = None
        self.m.notify(self.now + timedelta(minutes=2))
        self.assertEqual(len(self.t.sent()), 1)

    def test_update_offset_committed_before_reply_and_repeated_update_deduplicated(self):
        self.baseline()
        self.t.updates = [update(10, "/budget")]
        original = self.client.send_message
        def check(text):
            self.assertEqual(self.m._get("offset"), 11)
            return original(text)
        self.client.send_message = check
        self.m.poll_and_notify(self.now + timedelta(seconds=2))
        self.m.poll_and_notify(self.now + timedelta(seconds=4))
        self.assertEqual(len(self.t.sent()), 1)
        self.assertEqual(self.t.calls[-1][2]["offset"], 11)

    def test_get_updates_429_sets_persisted_cooldown(self):
        self.t.get_result = {"status_code": 429, "body": {"ok": False, "error_code": 429, "parameters": {"retry_after": 60}}}
        self.assertEqual(self.m.poll_and_notify(self.now)["status"], "telegram_rate_limited")
        self.assertEqual(self.m.poll_and_notify(self.now + timedelta(seconds=20))["status"], "cooldown")
        self.assertEqual(len(self.t.calls), 1)

    def test_webhook_conflict_does_not_delete_webhook(self):
        self.t.get_result = {"status_code": 409, "body": {"ok": False, "description": "webhook conflict"}}
        result = self.m.poll_and_notify(self.now)
        self.assertEqual(result["status"], "telegram_rejected")
        self.assertEqual([call[1] for call in self.t.calls], ["getUpdates"])

    def test_meaningful_account_budget_and_pause_recovery_alarms(self):
        self.baseline()
        self.s.db.execute("UPDATE experiments SET status='PAUSED',incident='platform_warning'")
        self.m.notify(self.now + timedelta(minutes=1))
        self.assertIn("사고 발생", self.t.sent()[-1]["text"])
        self.s.db.execute("UPDATE experiments SET status='RUNNING',incident=NULL")
        self.m.notify(self.now + timedelta(minutes=2))
        self.c.budget.infrastructure_hourly_usd = 9 / 168
        self.m.notify(self.now + timedelta(minutes=3))
        self.assertEqual(len(self.t.sent()), 3)
        self.assertIn("선택 작업 중단", self.t.sent()[-1]["text"])

    def test_expired_window_reports_once_and_never_polls_or_mutates_experiment(self):
        self.baseline()
        end = self.now + timedelta(hours=168)
        result = self.m.poll_and_notify(end)
        self.assertEqual(result["status"], "terminated")
        self.assertFalse(result["polling"])
        self.assertEqual(len(self.t.sent()), 1)
        self.assertIn("별도 중지", self.t.sent()[0]["text"])
        self.assertEqual(self.e.experiment()["status"], "RUNNING")
        self.c.budget.infrastructure_hourly_usd = .05
        self.m.poll_and_notify(end + timedelta(days=1))
        self.assertEqual(len(self.t.calls), 1)

    def test_terminal_notice_supersedes_old_pending_replies(self):
        self.baseline()
        self.m._queue("command:1", "old summary", self.now)
        self.s.db.execute("UPDATE experiments SET status='STOPPED'")
        self.m.poll_and_notify(self.now + timedelta(seconds=2))
        self.assertIn("운영자 중지", self.t.sent()[0]["text"])
        self.m.poll_and_notify(self.now + timedelta(seconds=4))
        self.assertEqual(len(self.t.sent()), 1)
        self.assertEqual(self.s.one("SELECT state FROM telegram_outbox WHERE event_key='command:1'")["state"], "REJECTED")

    def test_routing_stage_counts_no_prompt_or_provider_output(self):
        self.s.db.execute("CREATE TABLE IF NOT EXISTS routing_stage_runs(id TEXT PRIMARY KEY,stage TEXT,state TEXT,output_json TEXT)")
        self.s.db.execute("INSERT INTO routing_stage_runs(id,stage,state,output_json) VALUES('r','research','SUCCEEDED',?)", (TOKEN,))
        self.s.db.execute("INSERT INTO routing_stage_runs(id,stage,state,output_json) VALUES('p','planning','FAILED',?)", ("sensitive prompt",))
        text = self.m.preview(self.now)
        self.assertIn("리서치 1 완료", text)
        self.assertIn("기획 0 완료/1 확인", text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("sensitive prompt", text)

    def save_route_status(self, result, now=None):
        self.s.db.execute("CREATE TABLE IF NOT EXISTS routing_status(id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, result_json TEXT NOT NULL)")
        self.s.db.execute("INSERT INTO routing_status VALUES('latest',?,?) ON CONFLICT(id) DO UPDATE SET observed_at=excluded.observed_at,result_json=excluded.result_json",
                          (iso(now or self.now), json.dumps(result)))

    def test_live_routing_off_and_missing_tables_compatible(self):
        text = self.m.preview(self.now)
        self.assertIn("모델 라우팅: OFF", text)
        self.assertIn("최근 모델 작업: 아직 기록 없음", text)
        self.assertIn("모델 단계: 아직 실행 기록 없음", text)
        self.s.db.execute("CREATE TABLE routing_status(id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, result_json TEXT NOT NULL)")
        self.assertIn("최근 모델 작업: 아직 기록 없음", self.m.preview(self.now))

    def test_enabled_routing_missing_caps_reports_blocked(self):
        self.c.routing.enabled = True
        self.assertIn("차단 · 런타임·지출·출력 한도 검증 필요", self.m.preview(self.now))
        self.c.routing.expected_hermes_version = "reviewed runtime"
        self.c.routing.runtime_contract_verified = True
        self.c.routing.billing_cap_verified = True
        self.c.routing.output_cap_verified = True
        self.assertIn("설정 ON · 실제 연결은 별도 검증", self.m.preview(self.now))
        self.assertEqual(self.t.calls, [])

    def test_latest_route_guard_and_stopped_reason_are_fixed_safe_labels(self):
        self.save_route_status({"status": "blocked", "reason": "runtime_or_provider_cap_unverified", "stage": "research"})
        self.assertIn("최근 모델 작업: 차단 · 리서치 · 런타임·provider 한도 검증 미완료", self.m.preview(self.now))
        self.save_route_status({"status": "STOPPED", "stop_reason": "runtime_or_budget_guard", "stage": "execution"})
        self.assertIn("안전 조건으로 중단 · 실행 · 운영 시간 또는 예산 보호 조건 작동", self.m.preview(self.now))

    def test_latest_route_untrusted_fields_never_echo(self):
        self.save_route_status({"status": TOKEN, "reason": TOKEN, "stop_reason": TOKEN, "stage": TOKEN, "run_id": TOKEN})
        text = self.m.preview(self.now)
        self.assertNotIn(TOKEN, text)
        self.assertIn("상태 확인 필요", text)
        self.assertIn("상세 원인은 Cloud 콘솔 확인", text)
        self.m.notify(self.now)
        saved = json.dumps(self.m._get("snapshot"))
        self.assertNotIn(TOKEN, saved)
        self.save_route_status({"status": [TOKEN], "reason": {"private": TOKEN}, "stage": [TOKEN]})
        self.assertNotIn(TOKEN, self.m.preview(self.now))
        self.s.db.execute("UPDATE routing_status SET result_json='malformed private text'")
        self.assertIn("상태 확인 필요", self.m.preview(self.now))

    def test_latest_route_changes_notify_but_timestamps_reuse_and_run_ids_do_not(self):
        self.save_route_status({"status": "blocked", "reason": "budget_or_billing_guard"})
        self.baseline()
        later = self.now + timedelta(minutes=1)
        self.save_route_status({"status": "blocked", "reason": "budget_or_billing_guard", "reused": True, "run_id": "new-id"}, later)
        self.m.notify(later)
        self.assertEqual(self.t.sent(), [])
        self.save_route_status({"status": "STOPPED", "stop_reason": "runtime_or_budget_guard"}, later)
        self.m.notify(later + timedelta(minutes=1))
        self.assertEqual(len(self.t.sent()), 1)
        self.assertIn("안전 조건으로 중단", self.t.sent()[0]["text"])
        self.m.notify(later + timedelta(minutes=2))
        self.assertEqual(len(self.t.sent()), 1)

    def test_post_approval_scheduling_and_publication_count_changes_notify(self):
        slot = slots(self.c, self.now)[0]
        draft = self.e.draft(slot, self.now)
        self.baseline()
        self.e.approve(draft["id"])
        self.m.notify(self.now + timedelta(minutes=1))
        self.assertIn("승인 1", self.t.sent()[-1]["text"])
        self.s.db.execute("UPDATE post_drafts SET state='SCHEDULED' WHERE id=?", (draft["id"],))
        self.m.notify(self.now + timedelta(minutes=2))
        self.assertIn("예약 1", self.t.sent()[-1]["text"])
        self.s.db.execute("UPDATE post_drafts SET state='PUBLISHED' WHERE id=?", (draft["id"],))
        self.m.notify(self.now + timedelta(minutes=3))
        self.assertIn("게시 확인 1", self.t.sent()[-1]["text"])
        self.m.notify(self.now + timedelta(minutes=4))
        self.assertEqual(len(self.t.sent()), 3)

    def test_local_preview_does_not_contact_telegram(self):
        self.assertIn("실제 상태(게시 OFF)", self.m.preview(self.now))
        self.assertEqual(self.t.calls, [])
        self.assertEqual(self.s.rows("SELECT * FROM telegram_outbox"), [])


if __name__ == "__main__":
    unittest.main()
