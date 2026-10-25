from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys

from .config import Config, UTC, timestamp, iso
from .engine import Engine, GuardError
from .store import Store, process_lock
from .reporting import export


def parser():
    p = argparse.ArgumentParser(description="Disanchor: a bounded seven-day experiment; external actions disabled by default")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--state", default="state/gtm.sqlite")
    p.add_argument("--output", default="reports/live")
    p.add_argument("--env-file", help="Explicit owner-only KEY=value file; never executed as shell")
    p.add_argument("--no-publish", action="store_true", help="Staging lock: reject all Upload-Post writes, even if config enables publishing")
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("init", "pause", "stop", "inspect", "daily", "collect", "collect-mcp", "reconcile", "watchdog", "report", "dry-run", "preflight", "preview-library", "route", "telegram", "status", "connection-check", "account-prompts", "feedback", "learn"):
        commands.add_parser(name)
    commands.add_parser("start").add_argument("--at", required=True, help="Explicit timestamp with UTC offset; immutable")
    resume = commands.add_parser("resume")
    resume.add_argument("--clear-incident", action="store_true", help="Owner confirms platform issue resolved; publishing authorization must be renewed")
    commands.add_parser("approve").add_argument("post_id")
    auth = commands.add_parser("authorize")
    auth.add_argument("--reviewed-policy", action="store_true")
    auth.add_argument("--approve-library", action="store_true")
    auth.add_argument("--account-user", required=True)
    incident = commands.add_parser("incident")
    incident.add_argument("--reason", choices=["platform_warning", "owner_stop", "suspected_compromise"], required=True)
    cost = commands.add_parser("record-cost")
    cost.add_argument("--key", required=True)
    cost.add_argument("--category", choices=["infrastructure", "model", "external"], required=True)
    cost.add_argument("--classification", choices=["confirmed", "recorded", "pending", "estimate"], required=True)
    cost.add_argument("--amount", type=float)
    cost.add_argument("--supersedes", help="Append-only reconciliation of an earlier pending/estimated/recorded entry")
    imp = commands.add_parser("import-metrics")
    imp.add_argument("--file", required=True, help="JSON with post_id, observed_at, period_start, period_end, metrics; manually verified native insights only")
    return p


def live_engine(c, store, monitoring_only=False):
    from .bound_publisher import AccountBoundPublisher
    from .accounts import AccountRegistry
    from .analytics import GA4Client
    from .clone import CloneClient
    publisher = None
    # Read/cancel authenticated schedules remains available after auto_publish is disabled.
    if os.environ.get("UPLOAD_POST_API_KEY"):
        publisher = AccountBoundPublisher(os.environ["UPLOAD_POST_API_KEY"], AccountRegistry(c.accounts.registry_file),
                                         {"x": c.accounts.x_account, "threads": c.accounts.threads_account})
    if monitoring_only:
        # Status must remain available when optional GA4 dependencies or
        # credential files are broken. Provider construction does no network I/O.
        return Engine(c, store, publisher=publisher)
    ga4 = None
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and c.analytics.ga4_property_id:
        ga4 = GA4Client.from_service_account(c.analytics.ga4_property_id, include_content=True, key_event_registered=c.analytics.key_event_registered)
    elif os.environ.get("GA4_ACCESS_TOKEN") and c.analytics.ga4_property_id:
        ga4 = GA4Client(c.analytics.ga4_property_id, os.environ["GA4_ACCESS_TOKEN"], include_content=True, key_event_registered=c.analytics.key_event_registered)
    clone = CloneClient(enabled=c.clone.enabled, monthly_limit=c.clone.monthly_limit, mode=c.clone.mode,
                        session_id=c.experiment.id)
    feedback_factory = None
    if os.environ.get("UPLOAD_POST_API_KEY") and c.feedback.enabled:
        from .feedback import UploadPostFeedbackClient
        credential = os.environ["UPLOAD_POST_API_KEY"]
        feedback_factory = lambda posts: UploadPostFeedbackClient(credential, posts,
            max_pages=c.feedback.max_comment_pages, max_comments=c.feedback.max_comments_per_post,
            min_comments=c.feedback.minimum_comments)
    return Engine(c, store, publisher=publisher, ga4=ga4, clone=clone, feedback_factory=feedback_factory)


def dry_run(c, output):
    """Offline, deterministic integration demonstration, isolated from all live state."""
    from .publisher import MockPublisher
    from .analytics import MockGA4
    from .clone import MockClone
    from .routing import StageRouter, MockStageRunner
    dest = Path(output)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "simulation.sqlite"
    if path.exists():
        raise GuardError("Dry-run output already contains state; use a fresh output directory")
    c = replace(c, publishing=replace(c.publishing, account_remaining_quota=10))
    start = timestamp("2026-10-24T12:00:00Z")
    publisher = MockPublisher()
    with process_lock(path):
        s = Store(path)
        engine = Engine(c, s, publisher=publisher, ga4=MockGA4(), clone=MockClone(suggestion={"time": "17:30"}), simulated=True)
        engine.init(start)
        engine.start(start)
        router = StageRouter(engine, MockStageRunner(c))
        for day in range(7):
            now = start + timedelta(days=day)
            router.run(now)
            # A simulated owner's exact-text approval exercises the publishing
            # guard without approving or contacting any real account.
            for draft in s.rows("SELECT id FROM post_drafts WHERE state='DRAFT'"):
                engine.approve(draft["id"])
            engine.daily(now)
            engine.reconcile(now)
            engine.collect(now)
            # Explicit labeled fixture observations; never pass them through a real provider.
            for post in s.rows("SELECT * FROM post_drafts WHERE state='SCHEDULED'"):
                if timestamp(post["scheduled_at"]) < now + timedelta(days=1):
                    metrics = {"views": 100 + day * 11, "impressions": None, "likes": day + 1, "replies": 1,
                               "reposts": 0, "quotes": 0, "link_clicks": None, "followers": None}
                    s.record("post_metrics_snapshots", f"demo:{post['id']}", now + timedelta(hours=23), "SIMULATED fixture",
                             {"post_id": post["id"], "platform": post["platform"], "hypothesis": post["hypothesis"],
                              "period_start": post["scheduled_at"], "period_end": iso(now + timedelta(hours=23)),
                              "metrics": metrics, "missing": {"impressions": "not supplied in mock", "link_clicks": "unsupported", "followers": "not supplied"},
                              "provenance": "SIMULATED fixture; these posts were never published", "definition": "synthetic cumulative observations for collector/export testing"})
            export(engine, now, dest / "daily" / f"day-{day+1}")
        end = start + timedelta(hours=168)
        engine.watchdog(end)
        s.cost("mock-cost", end, "model", "estimate", 0.02, "SIMULATED fixture", iso(start), iso(end), "NO REAL CHARGE")
        result = export(engine, end, dest)
        s.close()
    return result


def import_metrics(engine, filename, now):
    import math
    body = json.loads(Path(filename).read_text())
    required = {"post_id", "observed_at", "period_start", "period_end", "metrics"}
    if not isinstance(body, dict) or set(body) != required:
        raise GuardError("Manual metrics require the exact documented schema")
    post = engine.s.one("SELECT * FROM post_drafts WHERE id=?", (body["post_id"],))
    if not post or post["state"] not in {"PUBLISHED", "CONFIRMED"}:
        raise GuardError("Manual insights require a verified published post ID")
    observed = timestamp(body["observed_at"])
    if observed > now or not timestamp(body["period_start"]) <= timestamp(body["period_end"]) <= observed:
        raise GuardError("Invalid observation period")
    allowed = {"views", "impressions", "reach", "likes", "replies", "comments", "reposts", "shares", "quotes", "link_clicks", "followers"}
    metrics = body["metrics"]
    if not isinstance(metrics, dict) or set(metrics) - allowed or any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0) for v in metrics.values()):
        raise GuardError("Invalid native metrics")
    engine.s.record("post_metrics_snapshots", f"manual:{body['post_id']}:{iso(observed)}", observed, "MANUAL platform insights", {
        **body, "platform": post["platform"], "hypothesis": post["hypothesis"],
        "account_key": post["account_key"], "account_fingerprint": post["account_fingerprint"],
        "hook_type": post["hook_type"], "published_at": post["scheduled_at"], "metrics": {k: metrics.get(k) for k in allowed},
        "missing": {k: "manual import omitted" for k in allowed if metrics.get(k) is None},
        "provenance": "MANUAL operator-imported platform insights", "definition": "native cumulative post metrics; not unique reach"})
    return {"status": "manual_observation_imported"}


def main(argv=None):
    args = parser().parse_args(argv)
    from .safety import publication_lock
    with publication_lock(args.no_publish):
        return run(args)


def run(args):
    try:
        c = Config.load(args.config)
        if args.no_publish:
            c = replace(c, publishing=replace(c.publishing, auto_publish=False, dry_run=True))
            if args.command in {"authorize", "approve", "resume", "start"}:
                raise GuardError("Cloud staging lock blocks experiment start and publication authorization")
        if args.env_file:
            from .environment import load_private_environment
            load_private_environment(args.env_file)
        now = datetime.now(UTC)
        if args.command == "dry-run":
            result = dry_run(c, args.output)
        elif args.command == "connection-check":
            from .accounts import AccountRegistry
            from .account_connection import check_connection
            registry = AccountRegistry(c.accounts.registry_file)
            bindings = []
            for key in (c.accounts.x_account, c.accounts.threads_account):
                row = registry.get(key)
                if row["native_account_id"]:
                    bindings.append({field: row[field] for field in ("id", "upload_post_profile", "platform", "native_account_id")})
            result = check_connection(api_key=os.environ.get("UPLOAD_POST_API_KEY"), bindings=bindings or None)
        elif args.command == "account-prompts":
            from .accounts import AccountRegistry
            registry = AccountRegistry(c.accounts.registry_file)
            result = {"status": "UNPUBLISHED account strategy preview", "network_calls": 0,
                      "selected": {"x": c.accounts.x_account, "threads": c.accounts.threads_account},
                      "accounts": [registry.context(row["id"]) for row in registry.all()]}
        elif args.command == "telegram" and not c.telegram.enabled:
            # Disabled monitoring does not read credentials or construct clients.
            result = {"status": "disabled", "network_calls": 0}
        elif args.command == "preview-library":
            from .policy import seed_text, validate_content
            from .config import slots
            samples = [seed_text(slot, c) for slot in slots(c, timestamp("2026-10-24T12:00:00Z"))]
            for sample in samples:
                validate_content(sample["platform"], sample["text"])
            result = {"status": "UNPUBLISHED candidate library for owner review", "posts": samples}
        elif args.command == "preflight":
            import shutil
            from .routing import STAGES, HermesStageRunner
            runner = HermesStageRunner(c.routing)
            stages = []
            for stage in STAGES:
                route = getattr(c.routing, stage)
                # --version / chat --help only; never generate or contact APIs.
                check = runner.preflight(stage, route)
                stages.append({"stage": stage, "provider": route.provider, "model": route.model,
                               "budget_share": route.budget_share,
                               "stage_budget_usd": round(c.budget.model_budget_usd * route.budget_share, 6),
                               "max_input_tokens": route.max_input_tokens, "max_output_tokens": route.max_output_tokens,
                               "max_cost_per_call_usd": route.max_cost_per_call_usd,
                               "runtime_status": check["status"], "runtime_reason": check.get("reason"),
                               "provider_model_access": "not_verified"})
            result = {"python": sys.version.split()[0], "hermes_present": bool(shutil.which("hermes")),
                      "publishing_default_disabled": c.publishing.dry_run or not c.publishing.auto_publish,
                      "upload_post_credential_present": bool(os.environ.get("UPLOAD_POST_API_KEY")),
                      "ga4_credential_present": bool(os.environ.get("GA4_ACCESS_TOKEN") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")),
                      "clone_credential_present": bool(os.environ.get("CLONE_APP_KEY")),
                      "routing": {"enabled": c.routing.enabled, "stages": stages,
                                  "max_pipelines_per_day": c.routing.max_pipelines_per_day,
                                  "max_stage_calls_per_day": c.routing.max_stage_calls_per_day,
                                  "model_budget_usd": c.budget.model_budget_usd},
                      "telegram": {"enabled": c.telegram.enabled, "daily_hour_kst": c.telegram.daily_hour,
                                   "bot_credential_present": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
                                   "private_chat_id_present": bool(os.environ.get("TELEGRAM_CHAT_ID")),
                                   "owner_user_id_present": bool(os.environ.get("TELEGRAM_USER_ID")),
                                   "connectivity": "not_verified"},
                      "model_execution": "runtime_contract_ready_provider_access_unverified" if all(item["runtime_status"] == "ready" for item in stages)
                                         else "disabled_or_blocked_by_runtime_and_cost_guards",
                      "network_calls": 0}
        else:
            with process_lock(args.state):
                s = Store(args.state)
                engine = live_engine(c, s, monitoring_only=args.command in {"status", "telegram", "feedback", "learn", "collect-mcp"})
                command = args.command
                if command == "init":
                    engine.init(now)
                    result = {"status": "initialized", "publishing": "disabled until all gates pass"}
                elif command == "start":
                    start = timestamp(args.at)
                    if start < now - timedelta(minutes=5):
                        raise GuardError("Live start timestamp cannot backdate more than five minutes")
                    engine.start(start)
                    result = engine.experiment()
                elif command == "authorize":
                    engine.authorize(now, args.reviewed_policy, args.approve_library, args.account_user)
                    result = {"status": "owner_authorization_recorded", "posted": False}
                elif command == "approve":
                    engine.approve(args.post_id)
                    result = {"status": "exact_draft_approved", "posted": False}
                elif command == "inspect":
                    result = {"experiment": engine.experiment(), "budget": engine.budget(now), "posts": s.rows("SELECT * FROM post_drafts")}
                elif command == "status":
                    from .telegram import TelegramMonitor
                    result = {"status": "local_preview", "text": TelegramMonitor(engine, None, daily_hour=c.telegram.daily_hour).preview(now),
                              "network_calls": 0}
                elif command == "feedback":
                    result = engine.feedback(now)
                elif command == "collect-mcp":
                    from .mcp_feedback import collect_mcp
                    result = collect_mcp(engine, now)
                elif command == "learn":
                    result = engine.learn(now)
                elif command == "telegram":
                    from .telegram import TelegramClient, TelegramMonitor
                    client = TelegramClient(os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                                            os.environ.get("TELEGRAM_CHAT_ID", ""),
                                            os.environ.get("TELEGRAM_USER_ID", ""))
                    result = TelegramMonitor(engine, client, enabled=True, daily_hour=c.telegram.daily_hour).poll_and_notify(now)
                elif command == "route":
                    from .routing import StageRouter
                    result = StageRouter(engine).run(now)
                elif command == "resume":
                    engine.resume(now, args.clear_incident)
                    result = {"status": "resumed", "end_at": engine.experiment()["end_at"]}
                elif command == "incident":
                    engine.incident(now, args.reason)
                    result = {"status": "paused_and_authorization_revoked", "check_provider_pending_jobs": True}
                elif command == "record-cost":
                    import math
                    if args.amount is not None and (not math.isfinite(args.amount) or args.amount < 0):
                        raise GuardError("Cost must be nonnegative and finite")
                    if not args.key.replace("-", "").replace("_", "").isalnum() or len(args.key) > 100:
                        raise GuardError("Cost key must be a short nonsecret identifier")
                    if args.supersedes and (not args.supersedes.replace("-", "").replace("_", "").isalnum() or len(args.supersedes) > 100):
                        raise GuardError("Reconciled key must be a short nonsecret identifier")
                    s.cost(args.key, now, args.category, args.classification, args.amount, "operator billing evidence", supersedes=args.supersedes)
                    result = {"status": "cost_appended"}
                elif command == "import-metrics":
                    result = import_metrics(engine, args.file, now)
                elif command == "report":
                    result = export(engine, now, args.output)
                else:
                    if command in {"daily", "collect", "reconcile"}:
                        watch = engine.watchdog(now)
                        if watch["status"] in {"STOPPED", "ENDED"}:
                            result = watch
                        else:
                            result = getattr(engine, command)(now)
                    else:
                        result = getattr(engine, command)(now)
                    export(engine, now, args.output)
                s.close()
        print(json.dumps(result, indent=2, default=str, allow_nan=False))
        return 0
    except (GuardError, ValueError, TypeError, OSError, RuntimeError) as exc:
        # Only local GuardError strings are shown; external exception bodies are never printed.
        message = str(exc) if isinstance(exc, GuardError) else "Configuration/runtime validation failed; check setup and use tests (secrets omitted)"
        print(json.dumps({"status": "blocked", "reason": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
