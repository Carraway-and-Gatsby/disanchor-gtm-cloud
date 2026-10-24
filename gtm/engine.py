from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import asdict
import hashlib
import json
import math
import time

from .config import Config, UTC, iso, timestamp, slots
from .policy import POLICY_VERSION, content_hash, validate_content, seed_text, valid_profile_link, require_action, SEEDS
from .store import Store
from .accounts import AccountRegistry


class GuardError(RuntimeError):
    pass


class Engine:
    def __init__(self, config: Config, store: Store, publisher=None, ga4=None, clone=None, simulated=False, feedback_factory=None):
        config.validate()
        self.c, self.s = config, store
        self.accounts = AccountRegistry(config.accounts.registry_file)
        for platform in ("x", "threads"):
            if self.accounts.get(self.account_key(platform))["platform"] != platform:
                raise GuardError("Account target platform mismatch")
        self.publisher, self.ga4, self.clone = publisher, ga4, clone
        self.feedback_factory = feedback_factory
        if feedback_factory and simulated and not getattr(feedback_factory, "simulated", False):
            raise GuardError("Simulation cannot use a live feedback client")
        self.simulated = simulated
        self.source = "SIMULATED" if simulated else "worker"
        if publisher and bool(getattr(publisher, "simulated", False)) != simulated:
            raise GuardError("Live and simulated publishers cannot be mixed")
        if ga4 and bool(getattr(ga4, "simulated", False)) != simulated:
            raise GuardError("Live and simulated analytics cannot be mixed")
        if clone and bool(getattr(clone, "simulated", False)) != simulated:
            raise GuardError("Live and simulated Clone clients cannot be mixed")

    def account_key(self, platform):
        if platform not in {"x", "threads"}:
            raise GuardError("Unsupported account platform")
        return getattr(self.c.accounts, platform + "_account")

    def account_context(self, platform):
        return self.accounts.context(self.account_key(platform))

    def account_fingerprint(self):
        if AccountRegistry(self.c.accounts.registry_file).fingerprint() != self.accounts.fingerprint():
            raise GuardError("Account registry changed; reload and review before continuing")
        return self.accounts.fingerprint()

    def check_draft_account(self, row):
        if (row.get("account_key") != self.account_key(row["platform"])
                or row.get("account_fingerprint") != self.account_fingerprint()):
            raise GuardError("Draft destination or account strategy changed; generate and review a new draft")

    def experiment(self):
        e = self.s.one("SELECT * FROM experiments WHERE id=?", (self.c.experiment.id,))
        if not e:
            raise GuardError("Initialize the experiment first")
        if e["mode"] != ("SIMULATED" if self.simulated else "LIVE"):
            raise GuardError("Mock and live state must be kept in separate databases")
        return e

    def init(self, now):
        if self.s.one("SELECT id FROM experiments WHERE id<>?", (self.c.experiment.id,)):
            raise GuardError("Use one database per experiment")
        self.s.db.execute("INSERT OR IGNORE INTO experiments(id,mode,config_hash) VALUES(?,?,?)",
                          (self.c.experiment.id, "SIMULATED" if self.simulated else "LIVE", self.c.fingerprint()))
        self.experiment()
        for key, text in (("H1", "Hardware dependence"), ("H2", "Workflow persistence")):
            self.s.record("audience_hypotheses", key, now, "owner brief", {"hypothesis": key, "description": text})
        self.s.log("initialized", now=now)

    def start(self, at):
        e = self.experiment()
        if e["start_at"]:
            raise GuardError("Start is immutable; resume never resets the 168-hour clock")
        start = timestamp(at)
        slots(self.c, start)  # Validate DST and boundaries before recording any start.
        self.s.db.execute("UPDATE experiments SET start_at=?,end_at=?,status='RUNNING' WHERE id=?",
                          (iso(start), iso(start + timedelta(hours=168)), e["id"]))
        self.s.log("started", now=start)

    def authorization_hash(self):
        data = asdict(self.c)
        # Billing reconciliation is routine evidence, not a change in publishing authorization.
        data["budget"].pop("reconciled_at")
        return hashlib.sha256(json.dumps({"config": data, "policy": POLICY_VERSION, "seeds": SEEDS,
                                         "accounts": self.account_fingerprint()}, sort_keys=True).encode()).hexdigest()

    def authorize(self, now, reviewed_policy=False, approve_library=False, account_user=""):
        if not reviewed_policy or not approve_library:
            raise GuardError("Owner must review policy, account configuration, and the seed library before authorizing")
        if not account_user or account_user != self.c.publishing.account_user or not self.c.publishing.account_reviewed:
            raise GuardError("Account identity has not been explicitly reviewed")
        if hasattr(self.publisher, "verify_destination"):
            for platform in ("x", "threads"):
                self.accounts.binding(self.account_key(platform))
        if not valid_profile_link(self.c.publishing.x_profile_url, self.c.landing.campaign):
            raise GuardError("Configure and manually verify the X profile UTM link first")
        self.experiment()
        self.s.db.execute("UPDATE experiments SET authorization_hash=?,authorized_at=? WHERE id=?",
                          (self.authorization_hash(), iso(now), self.c.experiment.id))
        self.s.log("authorized", now=now)

    def active(self, now):
        e = self.experiment()
        if not e["start_at"] or timestamp(now) < timestamp(e["start_at"]):
            raise GuardError("Experiment has not started")
        if timestamp(now) >= timestamp(e["end_at"]):
            self.stop(now, ended=True)
            raise GuardError("168-hour experiment window ended")
        if e["status"] != "RUNNING":
            raise GuardError("Experiment is not running")
        return e

    def budget(self, now):
        e, b = self.experiment(), self.c.budget
        ledger = self.s.rows("SELECT * FROM cost_ledger WHERE event_key NOT IN (SELECT original_key FROM cost_reconciliations)")
        non_infra = [r for r in ledger if r["category"] != "infrastructure"]
        spent = sum(r["amount_usd"] or 0 for r in non_infra)
        actual_infra = sum(r["amount_usd"] or 0 for r in ledger if r["category"] == "infrastructure" and r["classification"] != "estimate")
        hours = max(0, (min(timestamp(now), timestamp(e["end_at"])) - timestamp(e["start_at"])).total_seconds() / 3600) if e["start_at"] else 0
        accrued_infra = max(actual_infra, hours * b.infrastructure_hourly_usd)
        projected = spent + max(actual_infra, 168 * b.infrastructure_hourly_usd)
        accrued = spent + accrued_infra
        fresh = False
        if b.reconciled_at:
            age = (timestamp(now) - timestamp(b.reconciled_at)).total_seconds() / 3600
            fresh = 0 <= age <= b.reconciliation_max_age_hours
        unknown = any(r["amount_usd"] is None for r in ledger)
        verified = b.billing_verified and fresh and not unknown and b.infrastructure_hourly_usd > 0
        ratio = max(accrued, projected) / b.target_total_usd
        return {"source": "cost ledger and configured hourly estimate", "period_start": e["start_at"],
                "period_end": iso(now), "accrued_estimate_usd": round(accrued, 6),
                "confirmed_usd": sum(r["amount_usd"] or 0 for r in ledger if r["classification"] == "confirmed"),
                "projected_total_usd": round(projected, 6), "remaining_estimate_usd": round(b.target_total_usd - accrued, 6),
                "unknown_charges": unknown or not verified, "billing_verified": verified,
                "warning": ratio >= b.warning_threshold, "halt_optional": ratio >= b.stop_optional_work_threshold,
                "infrastructure_continues_after_end": True,
                "model_spend_usd": sum(r["amount_usd"] or 0 for r in non_infra if r["category"] == "model"),
                "external_spend_usd": sum(r["amount_usd"] or 0 for r in non_infra if r["category"] == "external")}

    def publishing_guard(self, now):
        e = self.active(now)
        if self.simulated:
            if not getattr(self.publisher, "simulated", False):
                raise GuardError("Simulation requires an explicitly simulated publisher")
            return
        p = self.c.publishing
        if p.dry_run or not p.auto_publish:
            raise GuardError("External posting remains disabled")
        if e["authorization_hash"] != self.authorization_hash():
            raise GuardError("Explicit owner authorization is missing or configuration changed")
        if not p.account_reviewed or not valid_profile_link(p.x_profile_url, self.c.landing.campaign):
            raise GuardError("Account/profile policy requirements are incomplete")
        if e["incident"]:
            raise GuardError("Account incident requires owner resolution")
        budget = self.budget(now)
        if budget["halt_optional"] or not budget["billing_verified"]:
            raise GuardError("Optional work stopped by budget or unverified billing")
        if not self.publisher:
            raise GuardError("Publishing credentials are unconfigured")

    def draft(self, slot, now):
        existing = self.s.one("SELECT * FROM post_drafts WHERE id=?", (slot["id"],))
        if existing:
            return existing
        seed = seed_text(slot, self.c)
        prior = [r["text"] for r in self.s.rows("SELECT text FROM post_drafts")]
        validate_content(seed["platform"], seed["text"], prior)
        self.s.record("content_ideas", seed["id"], now, "reviewable seed library", seed)
        approved = self.simulated or self.experiment()["authorization_hash"] == self.authorization_hash()
        digest = content_hash(seed["text"])
        approval = hashlib.sha256(seed["text"].encode()).hexdigest()
        columns = ("id", "experiment_id", "platform", "hypothesis", "audience", "hook_type", "cta_type", "variant", "text", "content_hash", "created_at", "scheduled_at", "local_date", "state", "approved_hash", "account_key", "account_fingerprint")
        values = (seed["id"], self.c.experiment.id, seed["platform"], seed["hypothesis"], seed["audience"], seed["hook_type"], seed["cta_type"], seed["variant"], seed["text"], digest, iso(now), seed["scheduled_at"], seed["local_date"], "APPROVED" if approved else "DRAFT", approval if approved else None, self.account_key(seed["platform"]), self.account_fingerprint())
        self.s.db.execute(f"INSERT INTO post_drafts({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
        return self.s.one("SELECT * FROM post_drafts WHERE id=?", (seed["id"],))

    def approve(self, post_id):
        row = self.s.one("SELECT * FROM post_drafts WHERE id=?", (post_id,))
        if not row or row["state"] != "DRAFT":
            raise GuardError("Only existing DRAFT posts can be approved")
        self.check_draft_account(row)
        validate_content(row["platform"], row["text"], [r["text"] for r in self.s.rows("SELECT text FROM post_drafts WHERE id<>?", (post_id,))])
        self.s.db.execute("UPDATE post_drafts SET state='APPROVED',approved_hash=? WHERE id=?", (hashlib.sha256(row["text"].encode()).hexdigest(), post_id))

    def schedule(self, post_id, now):
        require_action("schedule_own_text")
        self.publishing_guard(now)
        post = self.s.one("SELECT * FROM post_drafts WHERE id=?", (post_id,))
        if not post:
            raise GuardError("Unknown post")
        if self.s.one("SELECT id FROM publishing_jobs WHERE post_id=?", (post_id,)):
            return "existing_job_no_resubmission"
        self.check_draft_account(post)
        if post["state"] != "APPROVED" or post["approved_hash"] != hashlib.sha256(post["text"].encode()).hexdigest():
            raise GuardError("Draft needs exact-content approval")
        destination = None
        if hasattr(self.publisher, "verify_destination"):
            try:
                destination = self.publisher.verify_destination(post["platform"])
                expected = self.accounts.binding(self.account_key(post["platform"]))
                if not isinstance(destination, dict) or destination.get("binding") != expected:
                    self.incident(now, "provider_account_or_rate_limit")
                    raise GuardError("Publisher destination does not match the approved draft")
            except Exception as exc:
                if (getattr(exc, "code", None) == "account_identity_changed"
                        or getattr(exc, "http_status", None) in (401, 403, 429)):
                    self.incident(now, "provider_account_or_rate_limit")
                raise GuardError("Destination identity could not be verified; no upload reserved") from None
        # Provider quota is read before the transaction, no mutation occurs until the reservation commits.
        remaining = None
        try:
            remaining = self.publisher.quota().get("remaining")
        except Exception as exc:
            if getattr(exc, "http_status", None) in (401, 403, 429):
                self.incident(now, "provider_account_or_rate_limit")
                raise GuardError("Provider quota check signaled an account or rate-limit issue") from None
        p = self.c.publishing
        with self.s.transaction():
            e = self.active(now)
            post = self.s.one("SELECT * FROM post_drafts WHERE id=?", (post_id,))
            if not post:
                raise GuardError("Unknown post")
            self.check_draft_account(post)
            if self.s.one("SELECT id FROM publishing_jobs WHERE post_id=?", (post_id,)):
                return "existing_job_no_resubmission"
            if post["state"] != "APPROVED" or post["approved_hash"] != hashlib.sha256(post["text"].encode()).hexdigest():
                raise GuardError("Draft needs exact-content approval")
            due = timestamp(post["scheduled_at"])
            from zoneinfo import ZoneInfo
            if due.astimezone(ZoneInfo(self.c.experiment.audience_timezone)).date().isoformat() != post["local_date"]:
                raise GuardError("Posting timestamp no longer matches the reserved local date")
            if not timestamp(now) < due < timestamp(e["end_at"]):
                raise GuardError("Missed or out-of-window slot; no catch-up burst")
            if due - timestamp(now) > timedelta(hours=24):
                raise GuardError("Rolling queue is limited to 24 hours")
            validate_content(post["platform"], post["text"], [r["text"] for r in self.s.rows("SELECT text FROM post_drafts WHERE id<>?", (post_id,))])
            jobs = self.s.rows("SELECT j.*, d.platform FROM publishing_jobs j JOIN post_drafts d ON d.id=j.post_id")
            reserved = sum(j["reserved_quota"] for j in jobs)
            cap = min(self.c.platforms.max_posts_total, p.account_remaining_quota)
            if reserved >= cap or (remaining is not None and (type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining < 1)):
                raise GuardError("Upload quota exhausted or unverified")
            if sum(j["reserved_quota"] for j in jobs if j["platform"] == post["platform"]) >= getattr(self.c.platforms, "max_posts_" + post["platform"]):
                raise GuardError("Platform allocation exhausted")
            key = f"{e['id']}:{post_id}:{post['content_hash'][:16]}"
            # UNKNOWN written before outbound I/O: a process crash can never lead to a blind retry.
            self.s.db.execute("INSERT INTO publishing_jobs(id,post_id,created_at,state,reserved_quota,payload) VALUES(?,?,?,'UNKNOWN',1,?)",
                              (key, post_id, iso(now), json.dumps({"external_id": key, "destination": destination})))
            self.s.db.execute("UPDATE post_drafts SET state='UNKNOWN' WHERE id=?", (post_id,))
        try:
            result = self.publisher.schedule(post["platform"], post["text"], post["scheduled_at"], key)
        except Exception as exc:
            code = getattr(exc, "code", "unknown")
            http = getattr(exc, "http_status", None)
            self.s.log("submission_unknown", now=now)
            if http in (401, 403, 429) or code in {"authentication_error", "plan_restriction", "rate_limit", "account_identity_changed"}:
                self.incident(now, "provider_account_or_rate_limit")
            return "UNKNOWN"
        self.apply_status(post_id, result, now)
        return self.s.one("SELECT state FROM post_drafts WHERE id=?", (post_id,))["state"]

    def apply_status(self, post_id, result, now):
        state = str(result.get("status", "unknown")).upper()
        aliases = {"COMPLETED": "PUBLISHED", "SUCCESS": "PUBLISHED", "PENDING": "SCHEDULED", "PROCESSING": "SCHEDULED", "CANCELED": "CANCELLED"}
        state = aliases.get(state, state)
        if state not in {"SCHEDULED", "PUBLISHED", "CONFIRMED", "FAILED", "UNKNOWN", "CANCELLED"}:
            state = "UNKNOWN"
        provider_id = result.get("provider_job_id")
        url = result.get("published_url")
        if state in {"PUBLISHED", "CONFIRMED"} and not (url and result.get("platform_post_id")):
            state = "UNKNOWN"  # A provider 'success' alone does not prove publication.
        if state == "SCHEDULED" and not provider_id:
            state = "UNKNOWN"
        if state == "CONFIRMED":
            state = "PUBLISHED"  # No platform read-back API has independently confirmed it yet.
        with self.s.transaction():
            self.s.db.execute("UPDATE post_drafts SET state=?,provider_job_id=COALESCE(?,provider_job_id),published_url=COALESCE(?,published_url) WHERE id=?", (state, provider_id, url, post_id))
            self.s.db.execute("UPDATE publishing_jobs SET state=?,provider_job_id=COALESCE(?,provider_job_id) WHERE post_id=?", (state, provider_id, post_id))
            if state == "PUBLISHED":
                self.s.db.execute("INSERT INTO platform_posts(post_id,platform_post_id,provider_request_id,published_url) VALUES(?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET platform_post_id=excluded.platform_post_id,provider_request_id=excluded.provider_request_id,published_url=excluded.published_url",
                                  (post_id, result.get("platform_post_id"), result.get("provider_request_id") or result.get("request_id"), url))

    def reconcile(self, now):
        self.experiment()
        if not self.publisher:
            return {"status": "unconfigured"}
        pending = self.s.rows("SELECT * FROM publishing_jobs WHERE state IN ('UNKNOWN','SCHEDULED')")
        history = None
        for job in pending:
            try:
                if job["provider_job_id"]:
                    result = self.publisher.status(job["provider_job_id"])
                else:
                    if history is None:
                        history = self.publisher.history()
                    matches = [item for item in history if item.get("external_id") == job["id"] or item.get("idempotency_key") == job["id"]]
                    if len(matches) != 1:
                        continue
                    result = matches[0]
                self.apply_status(job["post_id"], result, now)
            except Exception as exc:
                if getattr(exc, "http_status", None) in (401, 403, 429):
                    self.incident(now, "provider_account_or_rate_limit")
                    break
        return {"reconciled": len(pending), "unresolved": self.s.one("SELECT count(*) n FROM publishing_jobs WHERE state='UNKNOWN'")["n"]}

    def cancel_pending(self, now):
        jobs = self.s.rows("SELECT * FROM publishing_jobs WHERE state IN ('SCHEDULED','UNKNOWN')")
        for job in jobs:
            if not job["provider_job_id"] or not self.publisher:
                self.s.log("cancel_unknown", now=now)
                continue
            try:
                self.publisher.cancel(job["provider_job_id"])
                verified = self.publisher.status(job["provider_job_id"])
                if str(verified.get("status")).lower() in {"cancelled", "canceled"}:
                    self.apply_status(job["post_id"], verified, now)
                else:
                    self.s.log("cancel_unknown", now=now)
            except Exception:
                self.s.log("cancel_unknown", now=now)
        return self.s.one("SELECT count(*) n FROM publishing_jobs WHERE state IN ('SCHEDULED','UNKNOWN')")["n"]

    def pause(self, now):
        self.experiment()
        self.s.db.execute("UPDATE experiments SET status='PAUSED' WHERE id=? AND status='RUNNING'", (self.c.experiment.id,))
        self.s.log("paused", now=now)
        return {"unresolved_external_jobs": self.cancel_pending(now)}

    def resume(self, now, clear_incident=False):
        e = self.experiment()
        if e["status"] != "PAUSED" or timestamp(now) >= timestamp(e["end_at"]):
            raise GuardError("Only a paused, unexpired experiment may resume")
        if e["incident"] and not clear_incident:
            raise GuardError("Resolve platform issue and explicitly clear the incident before resume")
        self.s.db.execute("UPDATE experiments SET status='RUNNING',incident=NULL WHERE id=?", (e["id"],))
        self.s.log("resumed", now=now)

    def incident(self, now, code):
        self.s.db.execute("UPDATE experiments SET status='PAUSED',incident=?,authorization_hash=NULL WHERE id=?", (code, self.c.experiment.id))
        self.s.log("incident", code, now)
        self.cancel_pending(now)

    def stop(self, now, ended=False):
        e = self.experiment()
        self.s.db.execute("UPDATE experiments SET status=?,authorization_hash=NULL WHERE id=?", ("ENDED" if ended else "STOPPED", e["id"]))
        self.s.db.execute("UPDATE post_drafts SET state='CANCELLED' WHERE state IN ('DRAFT','APPROVED')")
        unresolved = self.cancel_pending(now)
        self.s.log("stopped", now=now)
        return {"status": "ENDED" if ended else "STOPPED", "unresolved_external_jobs": unresolved,
                "cloud_instance_stopped": False}

    def daily(self, now):
        started = time.monotonic()
        base_now = timestamp(now)
        current = lambda: base_now + timedelta(seconds=max(0, time.monotonic() - started))
        e = self.active(now)
        learning = self.learn(now)
        routing = {"status": "disabled"}
        if self.c.routing.enabled and not self.simulated:
            from .routing import StageRouter
            try:
                routing = StageRouter(self).run(now)
            except (ValueError, OSError):
                routing = {"status": "blocked", "reason": "invalid_or_unavailable_evidence"}
            now = current()
            e = self.active(now)
        upcoming = [slot for slot in slots(self.c, timestamp(e["start_at"])) if timestamp(now) < timestamp(slot["scheduled_at"]) <= timestamp(now) + timedelta(hours=24)]
        drafts = []
        for slot in upcoming:
            row = self.draft(slot, now)
            drafts.append(row["id"])
        day = int((timestamp(now) - timestamp(e["start_at"])).total_seconds() // 86400) + 1
        key = f"decision:{day}"
        if not self.s.one("SELECT id FROM experiment_decisions WHERE event_key=?", (key,)):
            snapshots = self.s.records("post_metrics_snapshots")
            context = {"goal": "Research H1 hardware dependence and H2 workflow persistence", "experiment_id": e["id"],
                       "day": day, "next_slot": upcoming[0] if upcoming else {},
                       "previous_posts": [{"id": p["id"], "text": p["text"]} for p in self.s.rows("SELECT id,text FROM post_drafts")[-4:]],
                       "metrics": [{"post_id": r["payload"]["post_id"], "metrics": r["payload"]["metrics"]} for r in snapshots[-6:]],
                       "constraints": ["one variable", "5 posts/channel", "no unsupported claims", "owner-approved content only",
                                       "all 7 days: useful conversation, no links or promotional CTAs",
                                       "every post explicitly discloses Disanchor is in development",
                                       "prioritize organic attention and substantive responses; no guaranteed virality"]}
            prediction = {"request_id": None, "suggestion": None, "status": "unconfigured"}
            if self.clone and (self.simulated or (self.c.clone.enabled and not self.budget(now)["halt_optional"] and self.budget(now)["billing_verified"])):
                try:
                    prediction = self.clone.suggest(context)
                except Exception:
                    prediction = {"request_id": None, "suggestion": None, "status": "error"}
            self.s.record("clone_predictions", key, now, "SIMULATED Clone" if self.simulated else "Clone", prediction)
            # A bounded suggestion can inform the next slot; unreviewed generated copy is never published.
            suggestion = prediction.get("suggestion")
            accepted, changed = False, None
            if prediction.get("status") == "suggested" and isinstance(suggestion, dict) and set(suggestion) == {"time"} and upcoming:
                # Reschedule only within the approved day + window; local timezone is preserved.
                from zoneinfo import ZoneInfo
                from datetime import time as wall_time
                try:
                    original = timestamp(upcoming[0]["scheduled_at"]).astimezone(ZoneInfo(self.c.experiment.audience_timezone))
                    adjusted = original.replace(hour=wall_time.fromisoformat(suggestion["time"]).hour, minute=wall_time.fromisoformat(suggestion["time"]).minute)
                    candidate = adjusted.astimezone(UTC)
                    if (timestamp(now) < candidate < timestamp(e["end_at"]) and candidate <= timestamp(now) + timedelta(hours=24)
                        and candidate.astimezone(original.tzinfo).replace(tzinfo=None) == adjusted.replace(tzinfo=None)):
                        updated = self.s.db.execute("UPDATE post_drafts SET scheduled_at=? WHERE id=? AND state IN ('DRAFT','APPROVED')", (iso(candidate), upcoming[0]["id"])).rowcount
                        if updated == 1:
                            accepted, changed = True, {"variable": "publication_time", "from": iso(original), "to": iso(candidate)}
                except (ValueError, TypeError):
                    pass
            decision = {"day": day, "reason": "Insufficient independent observations; retain balanced exploratory allocation. The initial seven days prioritize useful organic conversation and observed views/engagement. Traffic and leads remain secondary observations; no sales CTA or automatic conversion push.",
                        "evidence_count": len(snapshots), "conclusion_supported": False,
                        "suggestion": suggestion, "accepted": accepted, "changed": changed,
                        "outcome": "pending later observations", "fallback": not accepted,
                        "next_action": "prepare approved seed or request owner review of new copy", "source": self.source}
            feedback = self.feedback(now)
            decision["feedback"] = {"observed_posts": len(feedback["posts"]),
                                    "directional_cohorts": sum(c["status"] == "directional_only" for c in feedback["cohorts"]),
                                    "skill_status": learning["status"]}
            if decision["feedback"]["directional_cohorts"]:
                decision["reason"] = "Within-account directional engagement observations inform research/planning. Preserve balanced exploration and test one variable; observational rankings are not causal winners or evidence of positive sentiment."
            self.s.record("experiment_decisions", key, now, self.source, decision)
            if self.clone and prediction.get("request_id") and prediction.get("status") == "suggested":
                try:
                    self.clone.feedback(prediction["request_id"], accepted=accepted)
                except Exception:
                    pass
        outcomes = {}
        for post_id in drafts:
            try:
                outcomes[post_id] = self.schedule(post_id, now if self.simulated else current())
            except GuardError:
                outcomes[post_id] = "blocked_by_guard"
        return {"drafts": drafts, "outcomes": outcomes, "model_generation": routing, "learning": learning}

    def feedback(self, now):
        """Read-only local aggregation; latest bounded observations, no HTTP."""
        from .feedback import aggregate_feedback
        self.experiment()
        f = self.c.feedback
        current = self.account_fingerprint()
        selected = {self.account_key(platform) for platform in ("x", "threads")}
        def matching(table):
            return [row for row in self.s.records(table)
                    if row["payload"].get("account_fingerprint") == current
                    and row["payload"].get("account_key") in selected]
        result = aggregate_feedback(matching("post_metrics_snapshots"), matching("sentiment_snapshots"),
                                    now=now, minimum_posts=f.minimum_posts,
                                    minimum_exposures=f.minimum_exposures, min_comments=f.minimum_comments)
        return {**result, "simulated": self.simulated, "network_calls": 0}

    def learning_loop(self):
        from .learning import LearningLoop, NativeSkillWriter
        config = self.c.learning
        writer = None
        if not self.simulated and config.hermes_source_dir and config.hermes_python:
            writer = NativeSkillWriter(config.hermes_source_dir, config.hermes_python)
        return LearningLoop(self.s.path.parent / "learning", native_writer=writer)

    def learn(self, now):
        """Regenerate local skill only from aggregate data; no model or HTTP call."""
        if not self.c.learning.enabled or not self.c.feedback.enabled:
            return {"status": "disabled", "network_calls": 0, "model_calls": 0}
        feedback = self.feedback(now)
        fingerprint = hashlib.sha256(json.dumps(feedback, sort_keys=True).encode()).hexdigest()
        self.s.record("marketing_feedback", "feedback:" + fingerprint, now, self.source, feedback)
        loop = None
        try:
            loop = self.learning_loop()
            result = loop.update(feedback, now=now, policy_version=POLICY_VERSION,
                                 account_fingerprint=self.account_fingerprint())
        except (ValueError, OSError, RuntimeError):
            result = {"status": "unavailable", "reason": "local_skill_validation_failed"}
        finally:
            if loop:
                loop.close()
        safe = {key: result[key] for key in ("status", "revision", "mechanism", "lesson_count", "evidence_status") if key in result}
        event = hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()
        self.s.record("learning_events", "learning:" + fingerprint + ":" + event, now, self.source, safe)
        return {**safe, "network_calls": 0, "model_calls": 0}

    def learning_context(self, now):
        if not self.c.learning.enabled or not self.c.feedback.enabled:
            return {}
        loop = None
        try:
            loop = self.learning_loop()
            return loop.context(policy_version=POLICY_VERSION,
                                account_fingerprint=self.account_fingerprint(), now=now)
        except (ValueError, OSError, RuntimeError):
            return {}
        finally:
            if loop:
                loop.close()

    def collect(self, now, final=False):
        e = self.experiment()
        if not e["start_at"]:
            raise GuardError("Start the experiment before collecting")
        end = min(timestamp(now), timestamp(e["end_at"]))
        bucket = int(timestamp(now).timestamp() // (self.c.analytics.minimum_collection_hours * 3600))
        key = f"collection:{'final' if final else bucket}"
        if self.s.one("SELECT id FROM website_metrics_snapshots WHERE event_key=?", (key,)):
            return {"status": "unchanged_interval_skipped"}
        if e["status"] in {"STOPPED", "ENDED"} and not final:
            return {"status": "terminated_no_new_collection"}
        start_date, end_date = timestamp(e["start_at"]).date().isoformat(), end.date().isoformat()
        website = {"source": "ga4", "status": "unconfigured", "totals": None,
                   "period": {"start_date": start_date, "end_date": end_date}, "missing": {"all": "GA4 credentials or website tracking unavailable"}}
        if self.ga4:
            try:
                website = self.ga4.collect(start_date, end_date, self.c.landing.campaign)
            except Exception:
                website["status"] = "unavailable"
        website["attribution_limitations"] = ["GA4 report dates use the property's reporting timezone and may include partial boundary days.", "Consent, blocking, processing delays, and GA4 retention can undercount visits; X profile visits are channel-level only."]
        self.s.record("website_metrics_snapshots", key, now, self.source + "/ga4", website)
        posts = self.s.rows("SELECT d.*,p.provider_request_id,p.platform_post_id FROM post_drafts d LEFT JOIN platform_posts p ON p.post_id=d.id")
        allowed_feedback = []
        for post in posts:
            metric_names = ("views", "impressions", "reach", "likes", "replies", "comments", "reposts", "shares", "quotes", "link_clicks", "followers")
            payload = {"post_id": post["id"], "platform": post["platform"], "hypothesis": post["hypothesis"],
                       "account_key": post["account_key"], "account_fingerprint": post["account_fingerprint"],
                       "hook_type": post["hook_type"], "published_at": post["scheduled_at"],
                       "period_start": post["created_at"], "period_end": iso(end),
                       "metrics": dict.fromkeys(metric_names), "missing": dict.fromkeys(metric_names, "not published or provider metric unavailable"),
                       "provenance": self.source, "definition": "cumulative per-post native observations; not unique reach"}
            if post["state"] in {"PUBLISHED", "CONFIRMED"} and self.publisher and post["provider_request_id"]:
                try:
                    response = self.publisher.analytics([post["provider_request_id"]])
                    matches = [m for m in response.get("posts", []) if m.get("platform") == post["platform"]]
                    if len(matches) == 1:
                        payload["metrics"].update({k: v for k, v in matches[0].get("metrics", {}).items() if k in metric_names})
                        payload["missing"] = matches[0].get("metric_missing_reasons", {})
                        payload["provenance"] = "Upload-Post platform analytics"
                except Exception:
                    self.s.log("collection_failed", now=now)
            self.s.record("post_metrics_snapshots", f"{key}:{post['id']}", now, payload["provenance"], payload)
            if post["state"] in {"PUBLISHED", "CONFIRMED"} and post["platform_post_id"]:
                try:
                    self.check_draft_account(post)
                    account = self.accounts.get(post["account_key"])
                    if account["native_account_id"]:
                        allowed_feedback.append({"post_id": post["id"], "account_key": post["account_key"],
                            "account_fingerprint": post["account_fingerprint"], "profile_username": account["upload_post_profile"],
                            "platform": post["platform"], "platform_post_id": post["platform_post_id"],
                            "native_account_id": account["native_account_id"]})
                except (GuardError, ValueError):
                    pass
        # Current own published IDs only. No history search, unsolicited replies or writes.
        if self.c.feedback.enabled and self.feedback_factory and allowed_feedback and not e["incident"]:
            try:
                client = self.feedback_factory(allowed_feedback[:self.c.feedback.max_posts_per_collection])
                if bool(getattr(client, "simulated", False)) != self.simulated:
                    raise GuardError("Feedback client mode mismatch")
                for post in allowed_feedback[:self.c.feedback.max_posts_per_collection]:
                    observation = client.collect(post["post_id"])
                    self.s.record("sentiment_snapshots", f"{key}:{post['post_id']}", now,
                                  self.source + "/Upload-Post comments", observation)
                    if observation.get("status") == "unavailable":
                        if observation.get("reason") == "rate_limited" and e["status"] in {"RUNNING", "PAUSED"}:
                            self.incident(now, "provider_account_or_rate_limit")
                        break  # No retries or more account reads after a provider/permission error.
            except Exception:
                self.s.log("collection_failed", now=now)
        leads = {"stored_leads": None, "qualified_leads": None, "reason": "No lead database configured", "period_start": start_date, "period_end": end_date}
        if not self.simulated and self.c.analytics.lead_db and Path(self.c.analytics.lead_db).is_file():
            try:
                from .leads import aggregate_leads
                leads = aggregate_leads(self.c.analytics.lead_db, e["start_at"], iso(end), self.c.landing.campaign,
                                        as_of=iso(now), retention_days=self.c.analytics.lead_retention_days)
            except Exception:
                leads["reason"] = "Lead aggregate unavailable"
        self.s.record("lead_aggregate_snapshots", key, now, self.source + "/server lead store", leads)
        if final:
            self.s.db.execute("UPDATE experiments SET final_collected=1 WHERE id=?", (e["id"],))
        learning = self.learn(now)
        return {"status": "collected", "mode": self.source, "learning": learning}

    def watchdog(self, now):
        e = self.experiment()
        if e["end_at"] and timestamp(now) >= timestamp(e["end_at"]):
            result = self.stop(now, ended=True)
            if not e["final_collected"]:
                self.reconcile(now)
                self.collect(now, final=True)
            return result
        return {"status": e["status"], "budget": self.budget(now)}
