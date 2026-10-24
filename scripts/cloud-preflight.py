#!/usr/bin/env python3
"""Read-only offline validation of the source bundle; no authenticated checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib

sys.dont_write_bytecode = True


ROOT_FILES = {"README.md", "README.ko.md", "config.toml", "pyproject.toml", "requirements.txt",
              "requirements-live.txt", ".env.example", ".gitignore", "accounts.example.toml"}
SOURCE_DIRS = {"gtm", "tests", "docs", "prompts", "scripts", "web", ".github"}
SOURCE_SUFFIXES = {".py", ".sh", ".md", ".toml", ".txt", ".html", ".js", ".css", ".yml", ".yaml"}
EXCLUDED_PARTS = {"state", "reports", "__pycache__", ".venv", "node_modules", ".git", ".aws", ".codex", ".agents", "secrets", "credentials"}
REQUIRED = {"README.md", "config.toml", "pyproject.toml", ".env.example", ".gitignore",
            "gtm/__main__.py", "gtm/config.py", "scripts/hermes-install.sh",
            "scripts/cloud-preflight.py", "scripts/package-cloud.py",
            "prompts/research.md", "prompts/planning.md", "prompts/content-system.md", "prompts/execution.md"}
SECRET_SIGNATURE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk-proj-|sk-ant-|clnp_|ghp_)[A-Za-z0-9_-]{20,}|\bAKIA[A-Z0-9]{16}\b|\b[0-9]{6,}:AA[A-Za-z0-9_-]{25,}")
SECRET_ASSIGNMENT = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*([^\n]*)$", re.M)


class ValidationError(ValueError):
    """Messages contain only fixed codes and relative source names."""


def _scan_source(relative, data):
    try:
        content = data.decode("utf-8")
    except UnicodeError:
        raise ValidationError("source_not_utf8:" + relative) from None
    if SECRET_SIGNATURE.search(content):
        raise ValidationError("possible_secret_signature:" + relative)
    if Path(relative).suffix not in {".py", ".js", ".sh", ".html", ".css"}:
        for match in SECRET_ASSIGNMENT.finditer(content):
            if Path(relative).suffix != ".toml" and match.group(1) != match.group(1).upper():
                continue
            if not re.search(r"(?:^|_)(?:token|api_key|secret|password)$", match.group(1), re.I):
                continue
            value = match.group(2).strip().strip("\"'")
            if value and not (value.startswith("<") and value.endswith(">")):
                raise ValidationError("nonempty_secret_assignment:" + relative)
    if relative == ".env.example":
        for line in content.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or value.strip():
                raise ValidationError("env_example_must_contain_only_empty_values")


def source_entries(root):
    """Read a strict source allowlist; never traverse symlinks or runtime data."""
    root = Path(root).resolve()
    candidates = []
    for name in sorted(ROOT_FILES):
        path = root / name
        if path.exists() or path.is_symlink():
            candidates.append(path)
    for name in sorted(SOURCE_DIRS):
        directory = root / name
        if directory.is_symlink():
            raise ValidationError("source_symlink:" + name)
        if not directory.exists():
            continue
        for current, directories, files in os.walk(directory, followlinks=False):
            retained = []
            for child in sorted(directories):
                path = Path(current) / child
                if child in EXCLUDED_PARTS or child.startswith("."):
                    continue
                if path.is_symlink():
                    raise ValidationError("source_symlink:" + path.relative_to(root).as_posix())
                retained.append(child)
            directories[:] = retained
            for child in sorted(files):
                path = Path(current) / child
                relative = path.relative_to(root)
                if child.startswith(".") or any(part in EXCLUDED_PARTS for part in relative.parts):
                    continue
                if path.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                if name == ".github" and (len(relative.parts) != 3 or relative.parts[1] != "workflows" or path.suffix not in {".yml", ".yaml"}):
                    continue
                if path.suffix not in {".py", ".sh", ".js", ".html", ".css"} and re.search(r"(?:^|[._-])(?:secrets?|credentials?|private[-_]key|service[-_]account|runtime[-_]env)(?:[._-]|$)", child, re.I):
                    continue
                candidates.append(path)
    entries, total = {}, 0
    for path in sorted(candidates):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise ValidationError("source_not_regular_file:" + relative)
        if metadata.st_size > 2 * 1024 * 1024:
            raise ValidationError("source_file_too_large:" + relative)
        data = path.read_bytes()
        total += len(data)
        if total > 10 * 1024 * 1024:
            raise ValidationError("source_bundle_too_large")
        _scan_source(relative, data)
        entries[relative] = data
    missing = REQUIRED - set(entries)
    if missing:
        raise ValidationError("required_source_missing:" + ",".join(sorted(missing)))
    if not any(name.startswith("tests/test_") and name.endswith(".py") for name in entries):
        raise ValidationError("test_suite_missing")
    return entries


def validate_config(root):
    # Import this validator's own worker code, not executable code from an
    # arbitrary --root tree. The selected tree contributes configuration data.
    tool_root = str(Path(__file__).resolve().parents[1])
    if tool_root not in sys.path:
        sys.path.insert(0, tool_root)
    try:
        from gtm.config import Config
        config = Config.load(root / "config.toml")
    except Exception:
        raise ValidationError("configuration_invalid") from None
    guards = {
        "publishing_disabled": not config.publishing.auto_publish and config.publishing.dry_run,
        "models_disabled": not config.routing.enabled and not config.model.enabled,
        "clone_disabled": not config.clone.enabled,
        "telegram_disabled": not config.telegram.enabled,
        "runtime_verification_unclaimed": not any((config.routing.runtime_contract_verified,
            config.routing.billing_cap_verified, config.routing.output_cap_verified)),
        "billing_verification_unclaimed": not config.budget.billing_verified,
    }
    if not all(guards.values()):
        raise ValidationError("bundle_requires_inactive_unverified_defaults:" + ",".join(k for k, v in guards.items() if not v))
    for label, value in (("routing_evidence", config.routing.evidence_file), ("lead_database", config.analytics.lead_db)):
        if value:
            # Local operational paths must be configured only on the Cloud host.
            raise ValidationError("bundle_contains_runtime_path:" + label)
    if hasattr(config, "accounts"):
        registry = tomllib.loads((root / "config.toml").read_text()).get("accounts", {}).get("registry_file", "accounts.example.toml")
        if registry != "accounts.example.toml" or not (root / registry).is_file():
            raise ValidationError("bundle_requires_example_account_registry")
    return guards


def check_runtime_file(root):
    state = root / "state"
    secret = state / "runtime.env"
    result = {"state_directory": "present" if state.is_dir() else "absent_not_required_for_bundle",
              "runtime_env": "absent_not_verified"}
    if state.is_symlink():
        raise ValidationError("state_directory_must_not_be_symlink")
    if secret.exists() or secret.is_symlink():
        metadata = secret.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_uid != os.getuid():
            raise ValidationError("runtime_env_requires_regular_owner_only_file")
        result["runtime_env"] = "private_file_present_contents_not_read"
    return result


def preflight(root, runtime_check=False):
    root = Path(root).resolve()
    if sys.version_info < (3, 12):
        raise ValidationError("python_3_12_or_newer_required")
    entries = source_entries(root)
    guards = validate_config(root)
    paths = check_runtime_file(root)
    runtime = {"checked": False, "model_compatibility_verified": False, "cloud_host_verified": False}
    if runtime_check:
        from gtm.process import bounded_run
        try:
            version = bounded_run(["hermes", "--version"], timeout=15, check=True).stdout.splitlines()
            help_text = bounded_run(["hermes", "cron", "create", "--help"], timeout=15, check=True).stdout
            required_flags = ("--no-agent", "--script", "--deliver", "--name", "--repeat", "--workdir")
            if not version or not all(flag in help_text for flag in required_flags):
                raise ValidationError("hermes_cron_contract_unsupported")
            runtime.update(checked=True, cron_flags_supported=True, version_sha256=hashlib.sha256(version[0].encode()).hexdigest())
        except Exception:
            raise ValidationError("hermes_runtime_read_check_failed") from None
    return {"status": "offline_checks_passed", "python": sys.version.split()[0],
            "source_files": len(entries), "source_bytes": sum(map(len, entries.values())),
            "default_guards": guards, "local_paths": paths, "hermes_runtime": runtime,
            "credentials_verified": False, "cloud_deployed": False,
            "account_bindings": "offline_template_only_live_readiness_unverified",
            "http_calls": 0, "model_calls": 0, "cron_mutations": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-check", action="store_true", help="Only read Hermes version and cron help on this host")
    args = parser.parse_args(argv)
    try:
        result = preflight(args.root, args.runtime_check)
    except (ValidationError, OSError) as error:
        result = {"status": "blocked", "reason": str(error) if isinstance(error, ValidationError) else "source_read_failed",
                  "credentials_verified": False, "cloud_deployed": False}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "offline_checks_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
