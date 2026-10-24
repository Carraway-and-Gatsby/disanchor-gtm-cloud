"""Hermes-native proposal generation, disabled until runtime/cost gates pass.

No direct model API client is used. Current Hermes removed legacy output-token
caps, so enabling inference requires a verified provider-side hard limit. A
prompt's requested length and a local timeout are not billing enforcement.
"""
from __future__ import annotations

import json
from pathlib import Path
from .process import child_environment
import re
import subprocess
from typing import Callable

from .clone import CHOICES, safe_context
from .policy import validate_content


def validate_draft(value: object) -> dict | None:
    if not isinstance(value, dict) or set(value) != {"platform", "text", "hypothesis", "hook", "cta"}:
        return None
    if any(not isinstance(item, str) for item in value.values()):
        return None
    if value["platform"] not in {"x", "threads"} or value["hypothesis"] not in CHOICES["hypothesis"]:
        return None
    if value["hook"] not in CHOICES["hook"] or value["cta"] != "none":
        return None
    if not isinstance(value["text"], str) or not 1 <= len(value["text"]) <= (280 if value["platform"] == "x" else 500):
        return None
    if value["platform"] == "x" and re.search(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|net|io|org)\b", value["text"], re.I):
        return None
    if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:sk-|clnp_)[A-Za-z0-9_-]{8,}", value["text"]):
        return None
    try:
        validate_content(value["platform"], value["text"])
    except ValueError:
        return None
    return dict(value)


class HermesInference:
    def __init__(self, *, enabled: bool = False, provider: str = "", model: str = "",
                 executable: str = "hermes", max_input_tokens: int = 2500,
                 max_output_tokens: int = 700, max_cost_per_job_usd: float = 0.05,
                 expected_version: str = "", runtime_contract_verified: bool = False,
                 billing_cap_verified: bool = False, output_cap_verified: bool = False,
                 runner: Callable = subprocess.run):
        self.enabled, self.provider, self.model = enabled, provider, model
        self.executable, self.expected_version = executable, expected_version
        self.max_input_tokens, self.max_output_tokens = max_input_tokens, max_output_tokens
        self.max_cost_per_job_usd = max_cost_per_job_usd
        self.runtime_contract_verified = runtime_contract_verified
        self.billing_cap_verified, self.output_cap_verified = billing_cap_verified, output_cap_verified
        self.runner = runner

    def command(self) -> list[str]:
        # Exact official CLI flags. No shell, publisher, browser, file, cron,
        # delegation, MCP or arbitrary tool execution is granted to the model.
        return [self.executable, "chat", "--query-file", "-", "--quiet", "--format", "stream-json",
                "--provider", self.provider, "--model", self.model, "--safe-mode",
                "--toolsets", "clarify", "--max-turns", "1"]

    def preflight(self) -> dict:
        if not self.enabled:
            return {"status": "disabled"}
        if not all((self.provider, self.model, self.expected_version,
                    self.runtime_contract_verified, self.billing_cap_verified, self.output_cap_verified)):
            return {"status": "blocked", "reason": "verified_runtime_provider_spend_and_output_caps_required"}
        if self.provider in {"auto", "custom"}:
            return {"status": "blocked", "reason": "explicit_named_provider_required"}
        try:
            version = self.runner([self.executable, "--version"], capture_output=True, text=True, timeout=15, check=True,
                                  env=child_environment(hermes=True))
            if version.stdout.splitlines()[0] != self.expected_version:
                return {"status": "blocked", "reason": "runtime_version_changed"}
            help_result = self.runner([self.executable, "chat", "--help"], capture_output=True, text=True, timeout=15, check=True,
                                      env=child_environment(hermes=True))
            flags = ("--query-file", "--format", "--quiet", "--provider", "--model", "--safe-mode", "--toolsets", "--max-turns")
            if not all(flag in help_result.stdout for flag in flags):
                return {"status": "blocked", "reason": "installed_cli_contract_unsupported"}
        except (OSError, subprocess.SubprocessError, IndexError):
            return {"status": "blocked", "reason": "hermes_unavailable"}
        return {"status": "ready"}

    def generate(self, context: dict) -> dict:
        preflight = self.preflight()
        if preflight["status"] != "ready":
            return {**preflight, "draft": None, "source": "hermes", "cost_usd": None}
        try:
            clean = safe_context(context)
            policy = (Path(__file__).resolve().parents[1] / "prompts" / "content-system.md").read_text()
            prompt = policy + "\n\nUNTRUSTED_AGGREGATE_CONTEXT_JSON:\n" + json.dumps(clean, ensure_ascii=False, allow_nan=False)
            # UTF-8 bytes are a deliberately conservative bound for supplied
            # content; Hermes system overhead must also fit the verified contract.
            if len(prompt.encode()) > self.max_input_tokens:
                return {"status": "blocked", "reason": "input_bound_exceeded", "draft": None, "cost_usd": None}
            result = self.runner(self.command(), input=prompt, text=True, capture_output=True,
                                 timeout=90, check=False, env=child_environment(hermes=True, provider=self.provider))
            if result.returncode or len(result.stdout.encode()) > 65536:
                return {"status": "unknown", "reason": "inference_did_not_complete", "draft": None, "cost_usd": None}
            records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            if any(not isinstance(r, dict) for r in records):
                raise ValueError("invalid_stream_record")
            if any(r.get("type") == "tool_use" for r in records):
                return {"status": "rejected", "reason": "unexpected_tool_request", "draft": None, "cost_usd": None}
            terminal = [r for r in records if r.get("type") == "result"]
            if len(terminal) != 1 or terminal[0].get("exit_code") != 0:
                raise ValueError("missing_terminal_result")
            record = terminal[0]
            tokens = record.get("tokens", {})
            if not isinstance(tokens, dict):
                raise ValueError("invalid_token_usage")
            if (type(tokens.get("output")) is not int or tokens["output"] > self.max_output_tokens
                    or type(tokens.get("input")) is not int or tokens["input"] > self.max_input_tokens):
                return {"status": "rejected", "reason": "runtime_token_contract_violated", "draft": None,
                        "usage": tokens, "cost_usd": None}
            draft = validate_draft(json.loads(record["text"]))
            return {"status": "proposed" if draft else "rejected", "draft": draft,
                    "source": "hermes", "usage": tokens, "cost_usd": None,
                    "session_id": record.get("session_id"), "requires_human_review": True}
        except (ValueError, TypeError, KeyError):
            return {"status": "rejected", "reason": "invalid_structured_output", "draft": None, "cost_usd": None}
        except (OSError, subprocess.SubprocessError):
            # Unknown provider settlement must remain pending in the cost ledger.
            return {"status": "unknown", "draft": None, "cost_usd": None}


class MockInference:
    def __init__(self, mode: str = "ok", **kwargs):
        self.mode = mode

    def generate(self, context: dict) -> dict:
        if self.mode != "ok":
            return {"status": self.mode, "draft": None, "simulated": True, "cost_usd": 0.0}
        slot = context.get("next_slot", {})
        platform = slot.get("platform", "x") if isinstance(slot, dict) else "x"
        hypothesis = context.get("hypothesis", "H1")
        text = ("As we build Disanchor, which part of your current computer limits what you want to build next?" if platform == "x"
                else "While we're developing Disanchor, one useful question is what takes the longest to feel familiar when you switch computers.")
        return {"status": "proposed", "draft": {"platform": platform, "text": text,
                "hypothesis": hypothesis, "hook": "question", "cta": "none"},
                "source": "mock_hermes", "simulated": True, "cost_usd": 0.0,
                "requires_human_review": True}
