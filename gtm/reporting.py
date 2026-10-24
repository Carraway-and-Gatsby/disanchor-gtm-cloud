from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import iso, timestamp


def _write_csv(path, rows, columns):
    # Spreadsheet-safe export: externally controlled strings cannot become formulas.
    def safe(value):
        if value is None:
            return "NULL"
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True)
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: safe(row.get(k)) for k in columns})


def export(engine, now, output):
    dest = Path(output)
    dest.mkdir(parents=True, exist_ok=True)
    e, s = engine.experiment(), engine.s
    budget = engine.budget(now)
    feedback = engine.feedback(now)
    learning_events = s.records("learning_events")
    learning = learning_events[-1]["payload"] if learning_events else {"status": "no_record"}
    posts = s.rows("SELECT * FROM post_drafts ORDER BY scheduled_at")
    post_records = s.records("post_metrics_snapshots")
    website_records = s.records("website_metrics_snapshots")
    lead_records = s.records("lead_aggregate_snapshots")
    decisions = s.records("experiment_decisions")
    has_routes = s.one("SELECT name FROM sqlite_master WHERE type='table' AND name='routing_runs'")
    routes = s.rows("SELECT * FROM routing_runs ORDER BY created_at") if has_routes else []
    stages = s.rows("SELECT * FROM routing_stage_runs ORDER BY created_at") if has_routes else []
    latest = {}
    for row in post_records:
        post_id = row["payload"]["post_id"]
        previous = latest.get(post_id)
        # A failed later fetch does not erase an earlier observation. Its original
        # observed_at remains visible in CSV; no stale value is called freshly observed.
        has_values = any(v is not None for v in row["payload"].get("metrics", {}).values())
        if not previous or has_values or not any(v is not None for v in previous["payload"].get("metrics", {}).values()):
            latest[post_id] = row
    web = website_records[-1]["payload"] if website_records else {}
    leads = lead_records[-1]["payload"] if lead_records else {}
    period = f"{e['start_at'] or 'not started'} → {min(iso(now), e['end_at']) if e['end_at'] else iso(now)}"
    mode = "SIMULATED — NO LIVE RESULTS" if engine.simulated else "LIVE STATE — unavailable measurements remain NULL"
    rows = []

    def metric(name, value, source, observed=period, note=""):
        rows.append({"metric": name, "value": "NULL" if value is None else value, "source": source, "period": observed, "note": note})

    metric("Experiment status", e["status"], "SQLite lifecycle", iso(now))
    metric("Feedback observed posts", len(feedback["posts"]), "latest own-post aggregates", iso(now), "Same account/platform/denominator only; unknown sentiment remains unknown")
    metric("Directional comparison groups", sum(c["status"] == "directional_only" for c in feedback["cohorts"]), "bounded feedback aggregation", iso(now), "Observational ranking; no causal winner")
    metric("Learning skill state", learning.get("status"), "append-only learning events", iso(now), learning.get("mechanism", "not generated"))
    metric("Model pipeline runs", len(routes), "SQLite routing_runs", iso(now), "SIMULATED" if engine.simulated else "Paid path requires runtime and provider cap verification")
    for stage in ("research", "planning", "writing", "execution"):
        records = [r for r in stages if r["stage"] == stage]
        metric(stage + " completed calls", sum(r["state"] == "SUCCEEDED" for r in records), "SQLite routing_stage_runs", iso(now), "Generated copy still requires exact-content approval")
    metric("Elapsed days", round(max(0, min(168, (timestamp(now) - timestamp(e["start_at"])).total_seconds()/3600))/24, 3) if e["start_at"] else None, "UTC start/end timestamps")
    reserved = s.one("SELECT COALESCE(sum(reserved_quota),0) n FROM publishing_jobs")["n"]
    metric("Local quota remaining", max(0, min(engine.c.platforms.max_posts_total, engine.c.publishing.account_remaining_quota) - reserved), "owner-configured account allowance minus durable reservations", note="Provider quota may be lower; cancellations do not restore local quota")
    for key in ("accrued_estimate_usd", "confirmed_usd", "projected_total_usd", "remaining_estimate_usd"):
        metric(key, budget[key], budget["source"], note="INCOMPLETE / unverified billing" if budget["unknown_charges"] else "estimate; confirmed charges shown separately")
    all_observed_views = []
    for platform in ("x", "threads"):
        platform_posts = [p for p in posts if p["platform"] == platform]
        metric(platform + " published posts", sum(p["state"] in ("PUBLISHED", "CONFIRMED") for p in platform_posts), "provider status stored in SQLite", note="SIMULATED" if engine.simulated else "Published IDs and URLs required")
        observed = [latest[p["id"]]["payload"] for p in platform_posts if p["id"] in latest and (engine.simulated or p["state"] in ("PUBLISHED", "CONFIRMED"))]
        native_period = "; ".join(sorted({str(r.get("period_start")) + " → " + str(r.get("period_end")) for r in observed})) or "No native observation"
        for key in ("views", "impressions", "likes", "replies", "reposts", "quotes", "link_clicks", "followers"):
            values = [r["metrics"].get(key) for r in observed]
            value = sum(values) if values and all(v is not None for v in values) and key != "followers" else None
            metric(platform + " " + key, value, "SIMULATED fixture" if engine.simulated else "latest available native per-post observations", native_period, note="NULL when incomplete; cumulative snapshots are never summed. Followers require profile-level observations.")
            if key == "views" and value is not None:
                all_observed_views.append(value)
        engagements = []
        for record in observed:
            m = record["metrics"]
            values = [m.get(k) for k in ("likes", "replies", "reposts", "quotes")]
            engagements.append(sum(values) if all(v is not None for v in values) else None)
        views = [r["metrics"].get("views") for r in observed]
        rate = (sum(engagements) / sum(views)) if engagements and all(v is not None for v in engagements + views) and sum(views) else None
        metric(platform + " engagement/view rate", rate, "same posts, latest native observations", note="likes+replies+reposts+quotes divided by native views; not unique people")
    totals = web.get("totals") or {}
    events = totals.get("event_counts") or {}
    for key in ("users", "sessions", "engaged_sessions"):
        metric("Campaign " + key, totals.get(key), "GA4 Data API", json.dumps(web.get("period", {})), "SIMULATED" if engine.simulated else "Observed; consent and processing delays apply")
    for key in ("page_view", "cta_click", "lead_form_start", "generate_lead"):
        metric("GA4 " + key, events.get(key), "GA4 Data API eventCount", json.dumps(web.get("period", {})))
    metric("GA4 key-event session rate: generate_lead", web.get("key_event_session_rate"), "GA4 Data API", json.dumps(web.get("period", {})), "NULL unless generate_lead key-event metric is configured and verified")
    metric("GA4 generate_lead key-event sessions", web.get("key_event_sessions"), "GA4 Data API", json.dumps(web.get("period", {})), "Unavailable until independent session-count query is verified; never infer from eventCount")
    metric("Server-valid research opt-ins", leads.get("stored_leads"), "server lead aggregate", json.dumps({k: v for k, v in leads.items() if k.startswith("period")}), "Syntax-validated, consented, deduplicated; mailbox ownership unverified")
    metric("Qualified leads", leads.get("qualified_leads"), "manual qualification", note="Contact validation is not qualification; no qualification workflow has run")
    for channel in web.get("channels", []) or []:
        metric("Attributed channel sessions: " + str(channel.get("source", "unknown")) + "/" + str(channel.get("medium", "unknown")), channel.get("sessions"), "GA4 sessionSource/sessionMedium", json.dumps(web.get("period", {})), "X profile-level only; do not assign to individual X posts")
    for content in web.get("content", []) or []:
        metric("Attributed content sessions: " + str(content.get("content_id", "unknown")), content.get("sessions"), "GA4 sessionManualAdContent", json.dumps(web.get("period", {})))
    def unit_cost(denominator, factor=1):
        return budget["accrued_estimate_usd"] * factor / denominator if denominator and not budget["unknown_charges"] else None
    metric("Cost / 1000 observed views USD", unit_cost(sum(all_observed_views), 1000) if len(all_observed_views) == 2 else None, "estimated accrued costs / observed native views", note="Sum of views is not unique reach; NULL if billing or view coverage is incomplete")
    metric("Cost / attributed session USD", unit_cost(totals.get("sessions")), "estimated accrued costs / GA4 campaign sessions")
    metric("Cost / server-valid lead USD", unit_cost(leads.get("stored_leads")), "estimated accrued costs / server-valid campaign leads")
    metric("Hypothesis winner", "INSUFFICIENT EVIDENCE", "small exploratory sample", note="No significance or causal claims; one high-view post does not reallocate channel quotas")

    daily_rows = [{"observed_at": r["observed_at"], "source": r["source"], "period": r["payload"].get("period"), "status": r["payload"].get("status", r["payload"].get("mode")), "totals": r["payload"].get("totals"), "missing": r["payload"].get("missing"), "mode": mode} for r in website_records]
    _write_csv(dest / "daily_metrics.csv", daily_rows, ["observed_at", "source", "period", "status", "totals", "missing", "mode"])
    post_rows = [{"observed_at": r["observed_at"], "source": r["source"], **r["payload"], "mode": mode} for r in post_records]
    _write_csv(dest / "post_metrics.csv", post_rows, ["post_id", "platform", "hypothesis", "observed_at", "source", "period_start", "period_end", "metrics", "missing", "provenance", "definition", "mode"])
    ledger = s.rows("SELECT c.*,r.replacement_key AS superseded_by FROM cost_ledger c LEFT JOIN cost_reconciliations r ON c.event_key=r.original_key ORDER BY c.id")
    _write_csv(dest / "cost_ledger.csv", ledger, ["event_key", "observed_at", "category", "classification", "amount_usd", "source", "period_start", "period_end", "note", "superseded_by"])
    (dest / "experiment_decisions.json").write_text(json.dumps({"mode": mode, "decisions": decisions}, indent=2), encoding="utf-8")
    (dest / "routing_runs.json").write_text(json.dumps({"mode": mode,
        "configured_routes": {name: asdict(getattr(engine.c.routing, name)) for name in ("research", "planning", "writing", "execution")},
        "runs": routes, "stages": stages}, indent=2), encoding="utf-8")
    from .telegram import TelegramMonitor
    (dest / "telegram-preview.txt").write_text("LOCAL PREVIEW — NOT SENT TO TELEGRAM\n\n" + TelegramMonitor(engine, None).preview(now) + "\n", encoding="utf-8")
    (dest / "marketing-feedback.json").write_text(json.dumps(feedback, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (dest / "learning-status.json").write_text(json.dumps(learning, indent=2) + "\n", encoding="utf-8")
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["# Disanchor seven-day experiment", "", f"**{mode}**", "", f"Generated: {timestamp(now).astimezone(ZoneInfo(engine.c.experiment.reporting_timezone)).isoformat()}", f"Start: {e['start_at']}", f"End: {e['end_at']}", "", "| Metric | Value | Source | Observation period | Caveat |", "|---|---|---|---|---|"]
    lines.extend("| " + " | ".join(esc(row[k]) for k in ("metric", "value", "source", "period", "note")) + " |" for row in rows)
    lines.extend(["", "## Feedback and learning", "", "[Account-specific feedback](marketing-feedback.json) · [Skill status](learning-status.json)", "", "Observed rankings are directional comparisons, not causal winners. Missing comments remain unknown. Raw comments and author identities are not retained."])
    lines.extend(["", "## Reviewable drafts", "", "| ID | State | Time UTC | Copy |", "|---|---|---|---|"])
    lines.extend("| " + " | ".join(esc(post[k]) for k in ("id", "state", "scheduled_at", "text")) + " |" for post in posts)
    lines.extend(["", "## Measurement gaps", "", "GA4 missing reasons: " + esc(json.dumps(web.get("missing", {}))), "GA4 warnings: " + esc(json.dumps(web.get("warnings", []))), "Attribution limitations: " + esc(json.dumps(web.get("attribution_limitations", []))), "Lead aggregate: " + esc(json.dumps(leads))])
    lines.extend(["", "## Decisions", "", *["- " + esc(r["payload"]["reason"]) + " Suggestion: " + esc(r["payload"]["suggestion"]) + "; change: " + esc(r["payload"]["changed"]) for r in decisions], "", "## Remaining limitations", "", "Provider integration tests use mocks. No deployment, live publication or live conversion is verified by this report. The Cloud instance is not stopped by experiment termination, and storage/runtime charges can continue. GA4 dates do not establish exact 168-hour attribution. Cancellation failures and UNKNOWN jobs require manual provider reconciliation."])
    (dest / "final_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    h = html.escape
    table = "".join("<tr>" + "".join(f"<td>{h(str(row[k]))}</td>" for k in ("metric", "value", "source", "period", "note")) + "</tr>" for row in rows)
    cards = "".join(f"<article><span>{h(p['id'])} · {h(p['state'])} · {h(p['hypothesis'])}</span><p>{h(p['text'])}</p><small>{h(p['scheduled_at'])}</small></article>" for p in posts)
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>Disanchor · Experiment report</title><style>body{{font:15px/1.5 system-ui;margin:0;background:#f4f5f1;color:#182922}}main{{max-width:1200px;margin:auto;padding:40px 24px}}h1{{font-size:42px;letter-spacing:-1.5px}}.flag{{background:#ffe3a2;padding:12px 18px;border-radius:6px}}small,span{{color:#456354}}table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{text-align:left;border-bottom:1px solid #cad6cd;padding:12px;vertical-align:top}}.scroll{{overflow:auto}}article{{background:white;border:1px solid #d2ddd6;padding:20px;border-radius:10px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}}a{{color:#155f46}}footer{{margin-top:32px;color:#456354}}</style><main><span>DISANCHOR / RESEARCH OPERATIONS</span><h1>Seven days. Measured honestly.</h1><p class="flag">{h(mode)}</p><p>{h(period)} · reports in Asia/Seoul</p><p><a href="final_report.md">Full report</a> · <a href="daily_metrics.csv">Daily observations</a> · <a href="post_metrics.csv">Post observations</a> · <a href="cost_ledger.csv">Costs</a> · <a href="experiment_decisions.json">Decisions</a> · <a href="marketing-feedback.json">Marketing feedback</a> · <a href="learning-status.json">Skill learning</a></p><h2>Evidence and limits</h2><div class="scroll"><table><thead><tr><th>Metric</th><th>Value</th><th>Source</th><th>Period</th><th>Caveat</th></tr></thead><tbody>{table}</tbody></table></div><h2>Content queue</h2><div class="grid">{cards}</div><footer>NULL means unavailable, never zero. Platform views are not unique people. This report does not verify live deployment. Termination does not stop Cloud billing.</footer></main></html>'''
    (dest / "index.html").write_text(document, encoding="utf-8")
    return {"output": str(dest.resolve()), "mode": mode, "files": ["index.html", "final_report.md", "daily_metrics.csv", "post_metrics.csv", "cost_ledger.csv", "experiment_decisions.json", "routing_runs.json", "telegram-preview.txt", "marketing-feedback.json", "learning-status.json"]}
