#!/usr/bin/env python3
"""Exercise the packaged credential-free staging in a disposable private tree."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_cloud", ROOT / "scripts/package-cloud.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)

CHILD = '''import os, sys
os.umask(0o077)
def audit(event, args):
    if event.startswith('socket.'):
        raise AssertionError("Network forbidden in staging smoke check")
sys.addaudithook(audit)
from gtm.__main__ import main
raise SystemExit(main(sys.argv[1:]))
'''


def smoke(root=ROOT):
    with tempfile.TemporaryDirectory(prefix="disanchor-staging-") as directory:
        temporary = Path(directory)
        output = temporary / "source.zip"
        bundle = package.build_bundle(root, output)
        stage = temporary / "workspace"
        stage.mkdir(mode=0o700)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                raise ValueError("unsafe_bundle_path")
            archive.extractall(stage)
        for name in ("state", "reports"):
            (stage / name).mkdir(mode=0o700)
        secret = stage / "state/runtime.env"
        secret.touch(mode=0o600)
        environment = {"PATH": os.defpath, "HOME": str(stage), "LANG": "C.UTF-8", "GTM_POSTING_LOCK": "1"}
        results = {}
        commands = (("init", ["init"], 0), ("status", ["status"], 0), ("telegram", ["telegram"], 0),
                    ("start", ["start", "--at", "2026-10-24T00:00:00Z"], 2))
        for name, args, expected in commands:
            result = subprocess.run([sys.executable, "-B", "-c", CHILD, "--no-publish", "--env-file", "state/runtime.env", *args],
                                    cwd=stage, env=environment, capture_output=True, text=True, timeout=30)
            if result.returncode != expected:
                raise ValueError("staging_command_failed:" + name)
            payload = json.loads(result.stdout if expected == 0 else result.stderr)
            if name == "status" and "게시 OFF" not in payload.get("text", ""):
                raise ValueError("staging_status_not_off")
            if name == "start" and payload.get("status") != "blocked":
                raise ValueError("staging_start_not_blocked")
            results[name] = "passed" if expected == 0 else "blocked_as_required"
        with sqlite3.connect(stage / "state/gtm.sqlite") as connection:
            status, start, authorization = connection.execute("SELECT status,start_at,authorization_hash FROM experiments").fetchone()
            jobs = connection.execute("SELECT count(*) FROM publishing_jobs").fetchone()[0]
        if status != "NEW" or start is not None or authorization is not None or jobs:
            raise ValueError("staging_state_changed_unexpectedly")
        return {"status": "offline_staging_smoke_passed", "commands": results,
                "bundle_sha256": bundle["sha256"], "credential_file_empty": secret.stat().st_size == 0,
                "state": status, "publishing_jobs": jobs, "network": "python_audit_hook_blocks_socket_events_in_child",
                "social_posts_sent": 0, "cloud_deployed": False, "cloud_host_verified": False}


if __name__ == "__main__":
    try:
        print(json.dumps(smoke(), indent=2, sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError):
        print(json.dumps({"status": "blocked", "reason": "offline_staging_check_failed"}))
        raise SystemExit(1)
