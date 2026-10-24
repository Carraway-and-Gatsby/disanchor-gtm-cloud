#!/usr/bin/env python3
"""Read-only checks for a credential-free, publication-locked Cloud staging."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import importlib.util
import json
import os
from pathlib import Path
import platform
import stat
import sys

sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("cloud_checks", Path(__file__).with_name("cloud-preflight.py"))
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


def private_metadata(path, directory=False):
    try:
        info = path.lstat()
        return (not path.is_symlink() and info.st_uid == os.getuid() and not info.st_mode & 0o077
                and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)))
    except OSError:
        return False


def credential_names(path):
    """Read an owner-private regular env file; return names, never values."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise checks.ValidationError("credential_file_not_private")
        raw = handle.read(32769)
    if len(raw) > 32768:
        raise checks.ValidationError("credential_file_too_large")
    names = set()
    for line in raw.decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator:
            raise checks.ValidationError("credential_file_format_invalid")
        if value.strip():
            names.add(name.strip().removeprefix("export "))
    return names


def budget_plan(hourly=None, stopped_daily=None, storage_days=7):
    if type(storage_days) is not int or not 0 <= storage_days <= 30:
        raise checks.ValidationError("invalid_storage_retention_days")
    result = {"total_limit_usd": 20, "warning_usd": 14, "optional_work_stop_usd": 18,
              "model_reserved_usd": 5, "tools_reserved_usd": 2, "unallocated_reserve_usd": 2,
              "running_hours": 168, "stopped_storage_days": storage_days,
              "provider_billing_cap_verified": False, "auto_shutdown_installed": False}
    if hourly is None or stopped_daily is None:
        return {**result, "status": "blocked_missing_current_prices", "projected_usd": None}
    try:
        hourly, stopped_daily = Decimal(str(hourly)), Decimal(str(stopped_daily))
        if any(not number.is_finite() or number < 0 or number > 20 for number in (hourly, stopped_daily)) or hourly == 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise checks.ValidationError("invalid_cloud_price") from None
    storage = stopped_daily * storage_days
    total = hourly * 168 + storage + 7
    return {**result, "status": "within_planning_envelope" if total <= 18 else "blocked_over_envelope",
            "projected_usd": float(total), "stopped_storage_reserved_usd": float(storage),
            "maximum_running_hourly_usd": float(max(Decimal(0), (Decimal(11) - storage) / 168)),
            "price_source": "operator_supplied_quote_not_independently_verified"}


def inspect_host(root, hermes_home=None, hermes_source=None, runtime_check=False,
                 hourly=None, stopped_daily=None, storage_days=7):
    root = Path(root).absolute()
    source = checks.preflight(root, runtime_check=runtime_check)
    controls = {"linux_host": platform.system() == "Linux", "non_root_worker": os.geteuid() != 0,
                "private_workspace": private_metadata(root, True),
                "private_state_directory": private_metadata(root / "state", True),
                "private_reports_directory": private_metadata(root / "reports", True),
                "private_runtime_env": private_metadata(root / "state/runtime.env")}
    names = credential_names(root / "state/runtime.env") if controls["private_runtime_env"] else None
    controls["staging_runtime_env_empty"] = names == set()
    forbidden = {"UPLOAD_POST_API_KEY", "GA4_ACCESS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
                 "CLONE_APP_KEY", "TELEGRAM_BOT_TOKEN", "OPENROUTER_API_KEY"}
    controls["staging_environment_credential_free"] = not any(os.environ.get(key) for key in forbidden)
    profiles = {}
    for label, location in (("hermes_home", hermes_home), ("hermes_source", hermes_source)):
        if location is None:
            profiles[label] = "not_inspected"
            continue
        folder = Path(location).absolute()
        if not private_metadata(folder, True):
            profiles[label] = "blocked_directory_permissions"
            continue
        env = folder / ".env"
        if env.exists() or env.is_symlink():
            try:
                names = credential_names(env)
                profiles[label] = "blocked_mixed_credentials" if names & (forbidden - {"OPENROUTER_API_KEY"}) else "env_names_checked"
            except (OSError, UnicodeError, checks.ValidationError):
                profiles[label] = "blocked_credential_file"
        else:
            profiles[label] = "no_dotenv_file"
    budget = budget_plan(hourly, stopped_daily, storage_days)
    profile_ok = all(value in {"no_dotenv_file", "env_names_checked"} for value in profiles.values())
    passed = (all(controls.values()) and profile_ok and source["hermes_runtime"]["checked"]
              and budget["status"] == "within_planning_envelope")
    return {"status": "staging_checks_passed" if passed else "blocked",
            "host_controls": controls, "hermes_profile_checks": profiles,
            "source_defaults": source["default_guards"], "runtime": source["hermes_runtime"],
            "budget_plan": budget, "cloud_identity_verified": False, "cloud_deployed": False,
            "live_activation_ready": False, "social_posts_sent": 0, "network_calls": 0,
            "unverified": ["Cloud identity and tenant isolation", "provider billing cap and current quote",
                           "credential stores beyond explicitly inspected .env files", "persistent restart/recovery",
                           "model output/spending caps", "Telegram owner-only delivery"],
            "scope": "Host-local checks only; a pass is not Cloud provider or production attestation"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--hermes-home", type=Path)
    parser.add_argument("--hermes-source", type=Path)
    parser.add_argument("--runtime-check", action="store_true")
    parser.add_argument("--running-hourly-usd")
    parser.add_argument("--stopped-daily-usd")
    parser.add_argument("--storage-days", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        result = inspect_host(args.root, args.hermes_home, args.hermes_source, args.runtime_check,
                              args.running_hourly_usd, args.stopped_daily_usd, args.storage_days)
    except (checks.ValidationError, OSError, UnicodeError):
        result = {"status": "blocked", "reason": "host_validation_failed_secrets_omitted", "cloud_deployed": False}
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["status"] == "staging_checks_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
