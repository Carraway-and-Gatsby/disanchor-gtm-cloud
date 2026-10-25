from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields, is_dataclass
from datetime import datetime, timedelta, timezone, time
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import tomllib
from urllib.parse import urlsplit

UTC = timezone.utc


def timestamp(value: str | datetime) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include an explicit UTC offset")
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    return timestamp(value).isoformat()


@dataclass
class Experiment:
    id: str = "disanchor_gtm_7d"
    duration_hours: int = 168
    reporting_timezone: str = "Asia/Seoul"
    audience_timezone: str = "America/New_York"


@dataclass
class Platforms:
    enabled: list[str] = field(default_factory=lambda: ["x", "threads"])
    max_posts_total: int = 10
    max_posts_x: int = 5
    max_posts_threads: int = 5
    max_posts_per_platform_per_day: int = 1
    x_time: str = "12:00"
    threads_time: str = "18:00"


@dataclass
class Publishing:
    provider: str = "upload_post"
    auto_publish: bool = False
    dry_run: bool = True
    allow_unsolicited_replies: bool = False
    allow_direct_messages: bool = False
    allow_email: bool = False
    allow_paid_ads: bool = False
    account_remaining_quota: int = 0
    account_user: str = ""
    x_profile_url: str = ""
    account_reviewed: bool = False


@dataclass
class Budget:
    target_total_usd: float = 20.0
    model_budget_usd: float = 5.0
    external_tools_budget_usd: float = 2.0
    warning_threshold: float = .70
    stop_optional_work_threshold: float = .90
    max_paid_model_jobs_per_day: int = 2
    infrastructure_hourly_usd: float = 0.0
    billing_verified: bool = False
    reconciled_at: str = ""
    reconciliation_max_age_hours: int = 24


@dataclass
class Landing:
    base_url: str = "https://disanchor.com"
    campaign: str = "disanchor_gtm_7d"


@dataclass
class Model:
    enabled: bool = False
    model: str = ""
    max_input_tokens: int = 2500
    max_output_tokens: int = 700
    max_cost_per_job_usd: float = .05


@dataclass
class Clone:
    enabled: bool = False
    mode: str = "sandbox"
    monthly_limit: int = 0


@dataclass
class Analytics:
    ga4_property_id: str = ""
    lead_db: str = ""
    minimum_collection_hours: int = 6
    key_event_registered: bool = False
    lead_retention_days: int = 30


@dataclass
class StageRoute:
    provider: str = "openrouter"
    model: str = "google/gemini-2.5-pro"
    budget_share: float = .4
    max_input_tokens: int = 12000
    max_output_tokens: int = 1800
    max_cost_per_call_usd: float = .12


@dataclass
class Routing:
    enabled: bool = False
    expected_hermes_version: str = ""
    runtime_contract_verified: bool = False
    billing_cap_verified: bool = False
    output_cap_verified: bool = False
    max_pipelines_per_day: int = 2
    max_stage_calls_per_day: int = 8
    evidence_file: str = ""
    research: StageRoute = field(default_factory=StageRoute)
    planning: StageRoute = field(default_factory=lambda: StageRoute(max_input_tokens=10000, max_output_tokens=1400))
    writing: StageRoute = field(default_factory=lambda: StageRoute(model="google/gemini-2.5-flash-lite", budget_share=.15, max_input_tokens=9000, max_output_tokens=1000, max_cost_per_call_usd=.045))
    execution: StageRoute = field(default_factory=lambda: StageRoute(model="google/gemini-2.5-flash-lite", budget_share=.05, max_input_tokens=9000, max_output_tokens=300, max_cost_per_call_usd=.015))


@dataclass
class Telegram:
    enabled: bool = False
    daily_hour: int = 9


@dataclass
class Accounts:
    registry_file: str = "accounts.example.toml"
    x_account: str = "disanchor_x"
    threads_account: str = "default_threads"


@dataclass
class Feedback:
    enabled: bool = True
    max_posts_per_collection: int = 10
    max_comments_per_post: int = 40
    max_comment_pages: int = 2
    minimum_posts: int = 3
    minimum_exposures: int = 100
    minimum_comments: int = 5


@dataclass
class Learning:
    enabled: bool = True
    hermes_source_dir: str = ""
    hermes_python: str = ""


@dataclass
class Config:
    experiment: Experiment = field(default_factory=Experiment)
    platforms: Platforms = field(default_factory=Platforms)
    publishing: Publishing = field(default_factory=Publishing)
    budget: Budget = field(default_factory=Budget)
    landing: Landing = field(default_factory=Landing)
    model: Model = field(default_factory=Model)
    clone: Clone = field(default_factory=Clone)
    analytics: Analytics = field(default_factory=Analytics)
    routing: Routing = field(default_factory=Routing)
    telegram: Telegram = field(default_factory=Telegram)
    accounts: Accounts = field(default_factory=Accounts)
    feedback: Feedback = field(default_factory=Feedback)
    learning: Learning = field(default_factory=Learning)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
        types = {"experiment": Experiment, "platforms": Platforms, "publishing": Publishing,
                 "budget": Budget, "landing": Landing, "model": Model, "clone": Clone,
                 "analytics": Analytics, "routing": Routing, "telegram": Telegram, "accounts": Accounts,
                 "feedback": Feedback, "learning": Learning}
        if set(raw) - set(types):
            raise ValueError("Unknown configuration section")
        routing_raw = dict(raw.get("routing", {}))
        defaults = Routing()
        for name in ("research", "planning", "writing", "execution"):
            if name in routing_raw:
                routing_raw[name] = StageRoute(**{**asdict(getattr(defaults, name)), **routing_raw[name]})
        raw["routing"] = routing_raw
        result = cls(**{name: constructor(**raw.get(name, {})) for name, constructor in types.items()})
        result.validate()
        return result

    def validate(self):
        # TOML booleans and integers must not be interchangeable.
        def validate_types(item):
            default = type(item)()
            for f in fields(item):
                value, example = getattr(item, f.name), getattr(default, f.name)
                if is_dataclass(example):
                    if type(value) is not type(example):
                        raise ValueError(f"Invalid configuration type: {f.name}")
                    validate_types(value)
                elif isinstance(example, float):
                    if type(value) not in (float, int) or not math.isfinite(value):
                        raise ValueError(f"Invalid numeric configuration: {f.name}")
                elif type(value) is not type(example):
                    raise ValueError(f"Invalid configuration type: {f.name}")
        validate_types(self)
        e, p, b = self.experiment, self.platforms, self.budget
        if e.duration_hours != 168:
            raise ValueError("This experiment must last exactly 168 hours")
        for zone in (e.reporting_timezone, e.audience_timezone):
            ZoneInfo(zone)
        if p.enabled != ["x", "threads"]:
            raise ValueError("Only the X + Threads experiment is supported")
        if not (0 < p.max_posts_total <= 10 and 0 < p.max_posts_x <= 5 and 0 < p.max_posts_threads <= 5
                and p.max_posts_per_platform_per_day == 1):
            raise ValueError("Post caps may be lowered but not exceed 10 total / 5 each / 1 daily")
        for clock in (p.x_time, p.threads_time):
            if time.fromisoformat(clock).tzinfo:
                raise ValueError("Posting times must be local wall times")
        pub = self.publishing
        if pub.provider != "upload_post" or any((pub.allow_unsolicited_replies, pub.allow_direct_messages,
                                                pub.allow_email, pub.allow_paid_ads)):
            raise ValueError("Disallowed outgoing action or provider")
        if not 0 <= pub.account_remaining_quota <= 10:
            raise ValueError("Account quota must be verified conservatively, 0..10")
        if not (0 < b.warning_threshold < b.stop_optional_work_threshold < 1):
            raise ValueError("Budget thresholds must be increasing fractions")
        if min(b.target_total_usd, b.model_budget_usd, b.external_tools_budget_usd) < 0 or b.target_total_usd == 0:
            raise ValueError("Invalid budget")
        if b.target_total_usd > 20 or b.model_budget_usd > 5 or b.external_tools_budget_usd > 2:
            raise ValueError("Budget exceeds the approved total or category ceiling")
        if b.infrastructure_hourly_usd < 0 or not 0 <= b.max_paid_model_jobs_per_day <= 2:
            raise ValueError("Invalid infrastructure/model budget")
        if not 1 <= b.reconciliation_max_age_hours <= 24:
            raise ValueError("Billing reconciliation must be no older than 24 hours")
        if b.reconciled_at:
            timestamp(b.reconciled_at)
        url = urlsplit(self.landing.base_url)
        if url.scheme != "https" or url.hostname != "disanchor.com" or url.username or url.query or url.fragment:
            raise ValueError("Landing URL must be a clean https://disanchor.com URL")
        if self.clone.mode != "sandbox" or self.clone.monthly_limit < 0:
            raise ValueError("Production Clone billing is not enabled by this implementation")
        if self.model.max_input_tokens > 2500 or self.model.max_output_tokens > 700:
            raise ValueError("Model token limits exceed experiment bounds")
        if min(self.model.max_input_tokens, self.model.max_output_tokens, self.model.max_cost_per_job_usd) <= 0:
            raise ValueError("Model limits must be positive")
        if self.model.enabled and not self.model.model:
            raise ValueError("A model must be explicitly pinned")
        if self.analytics.minimum_collection_hours < 6:
            raise ValueError("Collection interval must be at least 6 hours")
        if not 1 <= self.analytics.lead_retention_days <= 90:
            raise ValueError("Lead retention must be 1..90 days and match the server")
        r = self.routing
        routes = [r.research, r.planning, r.writing, r.execution]
        if (not 1 <= r.max_pipelines_per_day <= 2 or not 4 <= r.max_stage_calls_per_day <= 8
            or (r.enabled and r.max_pipelines_per_day > b.max_paid_model_jobs_per_day)):
            raise ValueError("Routing is bounded to two model pipeline jobs and eight stage calls per day")
        if not math.isclose(sum(s.budget_share for s in routes), 1.0) or any(not 0 < s.budget_share < 1 for s in routes):
            raise ValueError("Stage budget shares must be positive and sum to one")
        if r.research.budget_share + r.planning.budget_share < .8:
            raise ValueError("Research and planning must retain at least 80 percent of the model allowance")
        for stage in routes:
            if stage.provider != "openrouter" or not stage.model or any(ch.isspace() for ch in stage.model) or ":free" in stage.model or stage.model.endswith(":auto"):
                raise ValueError("Each stage requires an explicitly pinned OpenRouter model")
            if not (1 <= stage.max_input_tokens <= 16000 and 1 <= stage.max_output_tokens <= 4000 and 0 < stage.max_cost_per_call_usd <= .25):
                raise ValueError("Stage token/cost cap exceeds the experiment contract")
        if self.model.enabled:
            raise ValueError("Legacy single-model execution is disabled; configure the four-stage routing section")
        if not 0 <= self.telegram.daily_hour <= 23:
            raise ValueError("Telegram daily_hour must be 0..23 in the reporting timezone")
        if not self.accounts.registry_file or not self.accounts.x_account or not self.accounts.threads_account:
            raise ValueError("Account registry and explicit channel targets are required")
        f = self.feedback
        if not (1 <= f.max_posts_per_collection <= 10 and 1 <= f.max_comments_per_post <= 40
                and 1 <= f.max_comment_pages <= 2 and 3 <= f.minimum_posts <= 10
                and 100 <= f.minimum_exposures <= 1000000 and 5 <= f.minimum_comments <= 40):
            raise ValueError("Feedback collection and evidence thresholds exceed experiment bounds")
        if bool(self.learning.hermes_source_dir) != bool(self.learning.hermes_python):
            raise ValueError("Native skill writer requires both reviewed runtime paths")
        for value in (self.learning.hermes_source_dir, self.learning.hermes_python):
            if value and not Path(value).is_absolute():
                raise ValueError("Native Hermes runtime paths must be absolute")

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


DAY_PLATFORMS = [("x", "threads"), ("x",), ("threads",), ("x", "threads"), ("x",), ("threads",), ("x", "threads")]


def slots(config: Config, start: datetime) -> list[dict]:
    start = timestamp(start)
    end = start + timedelta(hours=168)
    zone = ZoneInfo(config.experiment.audience_timezone)
    first_day = start.astimezone(zone).date()
    result = []
    counts = {"x": 0, "threads": 0}
    for day, platforms in enumerate(DAY_PLATFORMS):
        for platform in platforms:
            counts[platform] += 1
            wall = datetime.combine(first_day + timedelta(days=day), time.fromisoformat(getattr(config.platforms, platform + "_time")))
            local = wall.replace(tzinfo=zone, fold=0)
            utc = local.astimezone(UTC)
            # Nonexistent wall clock times must never silently roll over during DST.
            if utc.astimezone(zone).replace(tzinfo=None) != wall:
                raise ValueError("Posting time falls in a daylight-saving gap; select another time")
            if day == 0 and utc <= start:
                utc = start + timedelta(minutes=10 if platform == "x" else 20)
            if not start <= utc < end or utc.astimezone(zone).date() != first_day + timedelta(days=day):
                raise ValueError("Configured slot is outside the 168-hour window; start earlier or adjust times")
            result.append({"id": f"{platform}_{counts[platform]:02d}", "platform": platform,
                           "day": day + 1, "scheduled_at": iso(utc), "local_date": utc.astimezone(zone).date().isoformat()})
    return result
