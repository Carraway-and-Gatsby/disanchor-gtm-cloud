"""Read-only operator monitoring over the official Telegram Bot API.

No Telegram input reaches a model, shell, publisher, or experiment mutator.
The transport uses the same injectable signature as the publisher adapter:
(method, url, headers, body_bytes) -> {status_code, body}. All exceptions are
fixed safe codes. Outbound delivery is at-most-once after ambiguous failures.
"""
from __future__ import annotations

from datetime import timedelta
import json
import re
from urllib import error, request
from zoneinfo import ZoneInfo

from .config import iso, timestamp
from .policy import valid_profile_link


class TelegramError(RuntimeError):
    def __init__(self, code, *, http_status=None, retry_after=None):
        # Never accept caller-supplied/provider error text in a safe exception.
        self.code = code if code in {"telegram_transport_unknown", "telegram_protocol_unknown", "telegram_rejected", "telegram_rate_limited"} else "telegram_protocol_unknown"
        self.http_status, self.retry_after = http_status, retry_after
        super().__init__(self.code)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http(method, url, headers, body):
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.build_opener(_NoRedirect).open(req, timeout=12) as response:
            raw, status = response.read(1_048_577), response.status
    except error.HTTPError as exc:
        raw, status = exc.read(1_048_577), exc.code
    except Exception:
        raise TelegramError("telegram_transport_unknown") from None
    if len(raw) > 1_048_576:
        raise TelegramError("telegram_protocol_unknown")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise TelegramError("telegram_protocol_unknown") from None
    return {"status_code": status, "body": payload}


def _positive_id(value):
    if type(value) is int and 0 < value < 2**52:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,15}", value) and int(value) < 2**52:
        return int(value)
    raise ValueError("Telegram requires explicit positive numeric private chat and user IDs")


class TelegramClient:
    simulated = False

    def __init__(self, token, chat_id, user_id, transport=None):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}", token):
            raise ValueError("Telegram bot credential is missing or invalid")
        self._token = token
        self.chat_id, self.user_id = _positive_id(chat_id), _positive_id(user_id)
        # Private bot conversations identify the chat by the receiving user's ID.
        if self.chat_id != self.user_id:
            raise ValueError("Telegram private chat must match the allowlisted user ID")
        self._real_network = transport is None
        self._transport = transport or _http

    def _call(self, method, payload):
        try:
            result = self._transport("POST", f"https://api.telegram.org/bot{self._token}/{method}",
                                     {"Content-Type": "application/json"},
                                     json.dumps(payload, allow_nan=False).encode())
        except TelegramError:
            raise
        except Exception:
            raise TelegramError("telegram_transport_unknown") from None
        if not isinstance(result, dict) or type(result.get("status_code")) is not int:
            raise TelegramError("telegram_protocol_unknown")
        body, status = result.get("body"), result["status_code"]
        if not isinstance(body, dict) or type(body.get("ok")) is not bool:
            raise TelegramError("telegram_protocol_unknown")
        if status == 429 and body["ok"] is False and body.get("error_code") == 429:
            retry = body.get("parameters", {})
            retry = retry.get("retry_after") if isinstance(retry, dict) else None
            if type(retry) is not int or not 1 <= retry <= 86400:
                retry = 60
            raise TelegramError("telegram_rate_limited", http_status=429, retry_after=retry)
        if body["ok"] is False and status in {400, 401, 403, 404, 409}:
            raise TelegramError("telegram_rejected", http_status=status)
        if status != 200 or body["ok"] is not True or "result" not in body:
            raise TelegramError("telegram_protocol_unknown")
        return body["result"]

    def send_message(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 4096:
            raise ValueError("Telegram message must be bounded plain text")
        result = self._call("sendMessage", {"chat_id": self.chat_id, "text": text,
                              "link_preview_options": {"is_disabled": True},
                              "protect_content": True})
        if not isinstance(result, dict) or type(result.get("message_id")) is not int or result["message_id"] <= 0:
            raise TelegramError("telegram_protocol_unknown")
        return result["message_id"]

    def get_updates(self, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid Telegram update offset")
        result = self._call("getUpdates", {"offset": offset, "limit": 100, "timeout": 5,
                                            "allowed_updates": ["message"]})
        if not isinstance(result, list) or len(result) > 100:
            raise TelegramError("telegram_protocol_unknown")
        return result


_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_state (
 key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_outbox (
 id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
 text TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL CHECK(state IN ('PENDING','UNKNOWN','SENT','RETRY','REJECTED')),
 not_before TEXT, message_id INTEGER, error_code TEXT);
"""
_STATUS = {"NEW": "준비", "RUNNING": "실행 중", "PAUSED": "일시 중지", "ENDED": "실험 종료", "STOPPED": "운영자 중지"}
_POST_STATES = ("DRAFT", "APPROVED", "SCHEDULED", "PUBLISHED", "CONFIRMED", "FAILED", "UNKNOWN", "CANCELLED")
_STAGE_LABELS = {"research": "리서치", "planning": "기획", "writing": "작성", "execution": "실행"}
_TERMINAL = {"ENDED", "STOPPED"}
_ROUTE_STATUS = {"DISABLED": "비활성", "NO_NEW_SLOT": "새 게시 슬롯 없음", "BLOCKED": "차단",
                 "RUNNING": "실행 중", "AWAITING_REVIEW": "검토용 초안 준비", "REJECTED": "제안 거절",
                 "STOPPED": "안전 조건으로 중단", "FAILED": "실패", "UNKNOWN": "결과 미확인",
                 "NO_RECORD": "아직 기록 없음", "UNRECOGNIZED": "상태 확인 필요"}
_ROUTE_REASONS = {"invalid_or_unavailable_evidence": "근거 자료 확인 필요",
                  "daily_pipeline_limit": "일일 파이프라인 한도 도달",
                  "daily_stage_call_limit": "일일 단계 호출 한도 도달",
                  "budget_or_billing_guard": "예산 또는 청구 검증 조건 미충족",
                  "unresolved_model_outcome": "이전 모델 호출 결과 미확인",
                  "stage_allowance_exhausted": "해당 단계 예산 소진",
                  "runtime_or_provider_cap_unverified": "런타임·provider 한도 검증 미완료",
                  "runtime_or_budget_guard": "운영 시간 또는 예산 보호 조건 작동",
                  "unrecognized": "상세 원인은 Cloud 콘솔 확인"}


class TelegramMonitor:
    def __init__(self, engine, client, *, enabled=False, daily_hour=9):
        if type(enabled) is not bool or type(daily_hour) is not int or not 0 <= daily_hour <= 23:
            raise ValueError("Invalid Telegram monitoring configuration")
        if enabled and client is None:
            raise ValueError("Enabled Telegram monitoring requires configured credentials")
        if enabled and engine.simulated and isinstance(client, TelegramClient) and client._real_network:
            raise ValueError("Simulated experiment cannot send through the live Telegram transport")
        self.engine, self.client, self.enabled, self.daily_hour = engine, client, enabled, daily_hour
        self.s = engine.s
        self.s.db.executescript(_SCHEMA)

    def _get(self, key, default=None):
        row = self.s.one("SELECT value FROM telegram_state WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def _set(self, key, value):
        self.s.db.execute("INSERT INTO telegram_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, json.dumps(value, sort_keys=True, allow_nan=False)))

    def _queue(self, key, text, now, priority=0):
        self.s.db.execute("INSERT OR IGNORE INTO telegram_outbox(event_key,created_at,text,priority,state) VALUES(?,?,?,?,'PENDING')",
                          (key, iso(now), text, priority))

    def _snapshot(self, now):
        e, budget = self.engine.experiment(), self.engine.budget(now)
        counts = {row["state"]: row["n"] for row in self.s.rows("SELECT state,count(*) n FROM post_drafts GROUP BY state") if row["state"] in _POST_STATES}
        unresolved = self.s.one("SELECT count(*) n FROM publishing_jobs WHERE state='UNKNOWN'")["n"]
        status = e["status"] if e["status"] in _STATUS else "UNKNOWN"
        # Interpret an expired clock read-only; the watchdog owns the state transition.
        expired = bool(e["end_at"] and timestamp(now) >= timestamp(e["end_at"]))
        if expired and status not in _TERMINAL:
            status = "ENDED"
        from .safety import posting_locked
        pub = self.engine.c.publishing
        publishing = bool(not posting_locked() and status == "RUNNING" and e["start_at"] and timestamp(now) >= timestamp(e["start_at"])
                          and pub.auto_publish and not pub.dry_run and pub.account_reviewed
                          and valid_profile_link(pub.x_profile_url, self.engine.c.landing.campaign)
                          and e["authorization_hash"] == self.engine.authorization_hash()
                          and not e["incident"] and budget["billing_verified"] and not budget["halt_optional"]
                          and self.engine.publisher and pub.account_remaining_quota > 0)
        return {"status": status, "incident": bool(e["incident"]), "expired": expired,
                "unknown_jobs": unresolved, "posts": counts, "budget": budget,
                "mode": "모의 실행" if self.engine.simulated else f"실제 상태(게시 {'ON' if publishing else 'OFF'})",
                "end_at": e["end_at"], "routing": self._routing_state(), "route_status": self._routing_status(),
                "feedback": self._feedback_snapshot(now)}

    def _feedback_snapshot(self, now):
        result = {"posts": 0, "directional_cohorts": 0, "positive": 0, "negative": 0,
                  "mixed": 0, "unknown": 0, "rankings": [], "learning": "no_record", "mechanism": "none"}
        try:
            feedback = self.engine.feedback(now)
            result["posts"] = len(feedback["posts"])
            for post in feedback["posts"]:
                label = post["sentiment"]["label"]
                result[label if label in {"positive", "negative", "mixed", "unknown"} else "unknown"] += 1
            for cohort in feedback["cohorts"]:
                if cohort["status"] == "directional_only":
                    result["directional_cohorts"] += 1
                    result["rankings"].append({"account": cohort["account_key"], "platform": cohort["platform"],
                                               "posts": cohort["ranked_post_ids"][:3]})
            events = self.s.records("learning_events")
            if events:
                latest = events[-1]["payload"]
                result["learning"] = latest.get("status") if latest.get("status") in {"created", "updated", "unchanged", "disabled", "unavailable"} else "unavailable"
                result["mechanism"] = latest.get("mechanism") if latest.get("mechanism") in {"native_tool", "compiled"} else "none"
        except (ValueError, RuntimeError, OSError, KeyError, TypeError):
            result["learning"] = "unavailable"
        return result

    def _feedback_text(self, snap):
        f = snap["feedback"]
        mechanism = {"native_tool": "Hermes native skill_manage", "compiled": "native 형식 생성", "none": "실행 기록 없음"}[f["mechanism"]]
        text = (f"반응 집계: 최근 관측 글 {f['posts']} · 비교 가능 그룹 {f['directional_cohorts']}\n"
                f"댓글 표본 판정(글 수): 긍정 {f['positive']} · 부정 {f['negative']} · 혼합 {f['mixed']} · 미확인 {f['unknown']}\n"
                f"학습 스킬: {f['learning']} · {mechanism}")
        for ranking in f["rankings"][:2]:
            text += f"\n{ranking['account']}/{ranking['platform']} 참고 순위: " + ", ".join(ranking["posts"])
        return text + "\n표본·관측 기반이며 인과적 우승이나 전체 이용자 여론이 아닙니다."

    def _routing_state(self):
        # A missing routing table is a supported pre-migration state.
        table = self.s.one("SELECT name FROM sqlite_master WHERE type='table' AND name='routing_stage_runs'")
        if not table:
            return None
        rows = self.s.rows("SELECT stage,state,count(*) n FROM routing_stage_runs GROUP BY stage,state")
        return {stage: {state: sum(r["n"] for r in rows if r["stage"] == stage and r["state"] == state)
                       for state in ("SUCCEEDED", "FAILED", "UNKNOWN")}
                for stage in _STAGE_LABELS}

    def _routing_status(self):
        config = self.engine.c.routing
        mode = "simulated" if self.engine.simulated else "disabled" if not config.enabled else (
            "configured" if all((config.expected_hermes_version, config.runtime_contract_verified,
                                 config.billing_cap_verified, config.output_cap_verified)) else "blocked")
        status = {"mode": mode, "last_status": "NO_RECORD", "reason": None, "stage": None}
        if not self.s.one("SELECT name FROM sqlite_master WHERE type='table' AND name='routing_status'"):
            return status
        row = self.s.one("SELECT result_json FROM routing_status WHERE id='latest'")
        if not row:
            return status
        try:
            raw = row["result_json"]
            if not isinstance(raw, str) or len(raw) > 65536:
                raise ValueError("invalid_routing_status")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("invalid_routing_status")
            value = result.get("status")
            value = value.upper() if isinstance(value, str) else "UNRECOGNIZED"
            status["last_status"] = value if value in _ROUTE_STATUS else "UNRECOGNIZED"
            reason = result.get("reason", result.get("stop_reason"))
            if reason is not None:
                status["reason"] = reason if isinstance(reason, str) and reason in _ROUTE_REASONS else "unrecognized"
            stage = result.get("stage")
            status["stage"] = stage if isinstance(stage, str) and stage in _STAGE_LABELS else None
        except (ValueError, TypeError):
            status["last_status"] = "UNRECOGNIZED"
            status["reason"] = "unrecognized"
        # Only fixed enum keys leave this method; no timestamp/run ID/reused flag.
        return status

    def _routing(self, snap):
        route = snap["route_status"]
        mode = {"simulated": "모의 실행", "disabled": "OFF (설정 비활성)",
                "blocked": "차단 · 런타임·지출·출력 한도 검증 필요", "configured": "설정 ON · 실제 연결은 별도 검증"}[route["mode"]]
        latest = _ROUTE_STATUS[route["last_status"]]
        if route["stage"]:
            latest += " · " + _STAGE_LABELS[route["stage"]]
        if route["reason"]:
            latest += " · " + _ROUTE_REASONS[route["reason"]]
        prefix = f"모델 라우팅: {mode}\n최근 모델 작업: {latest}\n"
        stages = snap["routing"]
        if stages is None:
            return prefix + "모델 단계: 아직 실행 기록 없음"
        return prefix + "모델 단계: " + " · ".join(
            f"{label} {stages[stage]['SUCCEEDED']} 완료/{stages[stage]['FAILED'] + stages[stage]['UNKNOWN']} 확인"
            for stage, label in _STAGE_LABELS.items())

    def _budget_text(self, snap):
        b = snap["budget"]
        flag = "선택 작업 중단 기준 도달" if b["halt_optional"] else "주의 기준 도달" if b["warning"] else "한도 내"
        return (f"비용: 누적 추정 ${b['accrued_estimate_usd']:.2f} / 목표 ${self.engine.c.budget.target_total_usd:.2f}\n"
                f"확정 ${b['confirmed_usd']:.2f} · 종료 시 추정 ${b['projected_total_usd']:.2f}\n"
                f"예산: {flag} · 청구 검증 {'완료' if b['billing_verified'] else '미확인'}")

    def _posts_text(self, snap):
        p = snap["posts"]
        return (f"게시: 초안 {p.get('DRAFT',0)} · 승인 {p.get('APPROVED',0)} · 예약 {p.get('SCHEDULED',0)}\n"
                f"게시 확인 {p.get('PUBLISHED',0)+p.get('CONFIRMED',0)} · 실패 {p.get('FAILED',0)} · 취소 {p.get('CANCELLED',0)}\n"
                f"외부 게시 결과 미확인 {snap['unknown_jobs']}건")

    def _status_text(self, snap, title="Disanchor 현황"):
        end = timestamp(snap["end_at"]).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST") if snap["end_at"] else "미정"
        risk = "\n계정·게시 사고 발생: 콘솔에서 원인과 보류 작업을 확인하세요." if snap["incident"] else ""
        final = "\n실험이 종료되어 Telegram 명령 조회를 중단합니다. Cloud 서버 과금은 별도 중지가 필요합니다." if snap["status"] in _TERMINAL else ""
        return (f"{title}\n{snap['mode']} · {_STATUS.get(snap['status'], '상태 미확인')}\n종료 예정: {end}\n"
                f"{self._budget_text(snap)}\n{self._posts_text(snap)}\n{self._routing(snap)}\n{self._feedback_text(snap)}"
                f"{risk}{final}\n제품: 개발 중 · 초기 7일은 비광고성 콘텐츠")

    def preview(self, now):
        """Safe local preview; performs no network call or monitoring mutation."""
        return self._status_text(self._snapshot(now))

    def _notifications(self, now, daily=False):
        snap = self._snapshot(now)
        signature = {"status": snap["status"], "incident": snap["incident"], "unknown_jobs": snap["unknown_jobs"],
                     "warning": snap["budget"]["warning"], "halt_optional": snap["budget"]["halt_optional"],
                     "billing_verified": snap["budget"]["billing_verified"], "routing": snap["routing"],
                     "route_status": snap["route_status"], "posts": snap["posts"], "mode": snap["mode"],
                     "feedback": snap["feedback"]}
        local = timestamp(now).astimezone(ZoneInfo("Asia/Seoul"))
        with self.s.transaction():
            previous = self._get("snapshot")
            terminal = snap["status"] in _TERMINAL
            if terminal:
                self.s.db.execute("UPDATE telegram_outbox SET state='REJECTED',error_code='terminal_superseded' WHERE state IN ('PENDING','RETRY') AND priority<10")
            if previous != signature and not (terminal and self._get("terminal_queued", False)):
                seq = self._get("sequence", 0) + 1
                self._queue(f"transition:{seq}", self._status_text(snap, "Disanchor 상태 변경"), now,
                            10 if snap["status"] in _TERMINAL else 5)
                self._set("sequence", seq)
                self._set("snapshot", signature)
                if terminal:
                    self._set("terminal_queued", True)
                elif daily or local.hour >= self.daily_hour:
                    self._set("initial_daily", local.date().isoformat())
            if snap["status"] not in _TERMINAL and (daily or local.hour >= self.daily_hour):
                # A first status notice already contains the full daily summary.
                if previous is not None and self._get("initial_daily") != local.date().isoformat():
                    self._queue(f"daily:{local.date().isoformat()}", self._status_text(snap, "Disanchor 일일 요약"), now, 1)
                else:
                    self._set("initial_daily", local.date().isoformat())
        return snap

    def _flush(self, now):
        cooldown = self._get("cooldown_until")
        if cooldown and timestamp(now) < timestamp(cooldown):
            return "cooldown"
        row = self.s.one("SELECT * FROM telegram_outbox WHERE state IN ('PENDING','RETRY') AND (not_before IS NULL OR not_before<=?) ORDER BY priority DESC,id LIMIT 1", (iso(now),))
        if not row:
            return "idle"
        # Commit the ambiguous state BEFORE I/O. A process crash never resends it.
        self.s.db.execute("UPDATE telegram_outbox SET state='UNKNOWN',error_code=NULL WHERE id=?", (row["id"],))
        self._set("cooldown_until", iso(timestamp(now) + timedelta(seconds=1)))
        try:
            message_id = self.client.send_message(row["text"])
        except TelegramError as exc:
            if exc.http_status == 429:
                retry_at = iso(timestamp(now) + timedelta(seconds=exc.retry_after or 60))
                with self.s.transaction():
                    self.s.db.execute("UPDATE telegram_outbox SET state='RETRY',not_before=?,error_code=? WHERE id=?", (retry_at, exc.code, row["id"]))
                    self._set("cooldown_until", retry_at)
                return "rate_limited"
            state = "REJECTED" if exc.code == "telegram_rejected" else "UNKNOWN"
            self.s.db.execute("UPDATE telegram_outbox SET state=?,error_code=? WHERE id=?", (state, exc.code, row["id"]))
            return "rejected" if state == "REJECTED" else "delivery_unknown"
        except Exception:
            self.s.db.execute("UPDATE telegram_outbox SET error_code='telegram_transport_unknown' WHERE id=?", (row["id"],))
            return "delivery_unknown"
        self.s.db.execute("UPDATE telegram_outbox SET state='SENT',message_id=? WHERE id=?", (message_id, row["id"]))
        return "sent"

    def _command(self, update, snap):
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender, chat = message.get("from"), message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        if (type(chat.get("id")) is not int or type(sender.get("id")) is not int
            or chat.get("type") != "private" or chat["id"] != self.client.chat_id
            or sender["id"] != self.client.user_id or sender.get("is_bot") is not False
            or any(key in message for key in ("forward_origin", "forward_from", "forward_date", "forward_from_chat", "forward_sender_name", "sender_chat", "via_bot", "sender_business_bot", "business_connection_id"))):
            return None
        text = message.get("text")
        if text == "/status":
            return self._status_text(snap)
        if text == "/budget":
            return "Disanchor 예산\n" + self._budget_text(snap)
        if text == "/posts":
            return "Disanchor 게시 집계\n" + self._posts_text(snap)
        if text == "/feedback":
            return "Disanchor 마케팅 학습\n" + self._feedback_text(snap)
        if text == "/help":
            return "조회 명령: /status /budget /posts /feedback /help\n게시·승인·재개·모델 실행은 Cloud 콘솔에서 처리합니다. Telegram 입력은 실행되지 않습니다."
        return None

    def notify(self, now, daily=False):
        if not self.enabled:
            return {"status": "disabled", "network_calls": 0}
        self._notifications(now, daily=daily)
        return {"status": self._flush(now)}

    def poll_and_notify(self, now):
        if not self.enabled:
            return {"status": "disabled", "network_calls": 0}
        snap = self._notifications(now)
        if snap["status"] in _TERMINAL:
            return {"status": "terminated", "delivery": self._flush(now), "polling": False}
        cooldown = self._get("cooldown_until")
        if cooldown and timestamp(now) < timestamp(cooldown):
            return {"status": "cooldown"}
        offset, polling = self._get("offset", 0), "ok"
        try:
            updates = self.client.get_updates(offset)
        except TelegramError as exc:
            if exc.http_status == 429:
                self._set("cooldown_until", iso(timestamp(now) + timedelta(seconds=exc.retry_after or 60)))
            return {"status": exc.code}
        except Exception:
            return {"status": "telegram_transport_unknown"}
        valid = [u for u in updates if isinstance(u, dict) and type(u.get("update_id")) is int and u["update_id"] >= offset]
        for update in sorted(valid, key=lambda u: u["update_id"]):
            # Unknown/unauthorized input is acknowledged but never stored or echoed.
            reply = self._command(update, snap)
            with self.s.transaction():
                self._set("offset", update["update_id"] + 1)
                if reply:
                    self._queue(f"command:{update['update_id']}", reply, now, 3)
        return {"status": self._flush(now), "polling": polling}
