"""Installer regressions using a temporary fake Hermes; never install real jobs."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


FAKE_HERMES = '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ['GTM_TEST_JOB_LOG'])
jobs = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
if args == ['--version']:
    print('Hermes installer test runtime')
elif args == ['cron', 'create', '--help']:
    flags = '--no-agent --script --deliver --name --repeat --workdir'
    print(flags.replace(os.environ.get('GTM_TEST_MISSING_FLAG', '<none>'), ''))
elif args == ['cron', 'list']:
    print(os.environ.get('GTM_TEST_EXISTING_JOBS', ''))
    for job in jobs:
        print(job[job.index('--name') + 1])
elif args[:2] == ['cron', 'create']:
    if len(jobs) + 1 == int(os.environ.get('GTM_TEST_FAIL_AT', '0')):
        raise SystemExit('SIMULATED create failure')
    with log.open('a') as output:
        output.write(json.dumps(args) + '\\n')
    print('SIMULATED job-' + str(len(jobs) + 1))
else:
    raise SystemExit('Unexpected fake CLI command')
'''


class CloudInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="disanchor installer test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "project with spaces"
        (self.repo / "scripts").mkdir(parents=True)
        self.installer = self.repo / "scripts" / "hermes-install.sh"
        original = Path(__file__).resolve().parents[1] / "scripts" / "hermes-install.sh"
        shutil.copyfile(original, self.installer)
        (self.repo / "config.toml").write_text("")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fake = self.root / "fake_hermes.py"
        self.fake.write_text(FAKE_HERMES)
        executable = self.bin / "hermes"
        executable.write_text('#!/bin/sh\nexec "$GTM_TEST_PYTHON" "$GTM_TEST_FAKE" "$@"\n')
        executable.chmod(0o700)
        self.hermes_home = self.root / "hermes"
        self.wrappers = self.hermes_home / "scripts"
        self.job_log = self.root / "jobs.jsonl"
        # Never inherit real provider credentials or a real Hermes configuration.
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "HOME": str(self.root), "HERMES_HOME": str(self.hermes_home),
            "GTM_TEST_PYTHON": sys.executable, "GTM_TEST_FAKE": str(self.fake),
            "GTM_TEST_JOB_LOG": str(self.job_log), "GTM_PYTHON": sys.executable,
            "GTM_HOSTED_INSTANCE_CONFIRMED": "yes",
            "GTM_EXPECTED_HERMES_VERSION": "Hermes installer test runtime",
        }

    def call(self, mode=None, **extra):
        args = ["bash", str(self.installer)] + ([mode] if mode else [])
        return subprocess.run(args, env={**self.env, **extra}, capture_output=True,
                              text=True, timeout=15, check=False)

    def jobs(self):
        return [json.loads(line) for line in self.job_log.read_text().splitlines()] if self.job_log.exists() else []

    def install(self):
        result = self.call("--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_default_and_check_do_not_write_or_create_jobs(self):
        for mode in (None, "--check"):
            result = self.call(mode)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No files or schedules created", result.stdout)
            self.assertFalse(self.hermes_home.exists())
            self.assertEqual(self.jobs(), [])

    def test_operator_version_and_cli_capability_gates_precede_mutation(self):
        for changes in ({"GTM_HOSTED_INSTANCE_CONFIRMED": ""},
                        {"GTM_EXPECTED_HERMES_VERSION": "different runtime"},
                        {"GTM_TEST_MISSING_FLAG": "--no-agent"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.call("--install", **changes).returncode, 0)
                self.assertFalse(self.hermes_home.exists())
                self.assertEqual(self.jobs(), [])

    def test_existing_experiment_job_blocks_duplicate_install(self):
        result = self.call("--install", GTM_TEST_EXISTING_JOBS="id disanchor-gtm-daily paused")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refuses duplicate schedules", result.stderr)
        self.assertFalse(self.hermes_home.exists())
        self.assertEqual(self.jobs(), [])

    def test_six_bounded_local_no_agent_jobs_and_private_compilable_wrappers(self):
        result = self.install()
        jobs = self.jobs()
        self.assertEqual(len(jobs), 6)
        expected = {"daily": ("every 24h", "7"), "collect": ("every 8h", "21"),
                    "reconcile": ("every 4h", "42"), "watchdog": ("every 1h", "168"),
                    "report": ("every 24h", "7"), "telegram": ("every 5m", "2017")}
        names = set()
        for job in jobs:
            name = job[job.index("--name") + 1]
            self.assertNotIn(name, names)
            names.add(name)
            command = name.removeprefix("disanchor-gtm-")
            self.assertEqual((job[2], job[job.index("--repeat") + 1]), expected[command])
            self.assertIn("--no-agent", job)
            self.assertNotIn("--prompt", job)
            self.assertEqual(job[job.index("--deliver") + 1], "local")
            self.assertEqual(job[job.index("--workdir") + 1], str(self.repo))
            wrapper = self.wrappers / job[job.index("--script") + 1]
            compile(wrapper.read_text(), str(wrapper), "exec")
            self.assertEqual(wrapper.stat().st_mode & 0o777, 0o700)
        self.assertEqual(len(list(self.wrappers.iterdir())), 6)
        self.assertIn("six returned job IDs", result.stdout)
        self.assertNotEqual(self.call("--install").returncode, 0)
        self.assertEqual(len(self.jobs()), 6)

    def test_existing_last_wrapper_prevents_any_partial_wrapper_creation(self):
        self.wrappers.mkdir(parents=True)
        last = self.wrappers / "disanchor-gtm-telegram.py"
        last.write_text("# operator content\n")
        result = self.call("--install")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(last.read_text(), "# operator content\n")
        self.assertEqual(list(self.wrappers.iterdir()), [last])
        self.assertEqual(self.jobs(), [])

    def test_partial_scheduler_failure_stops_and_retry_cannot_duplicate_jobs(self):
        result = self.call("--install", GTM_TEST_FAIL_AT="3")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.jobs()), 2)
        self.assertEqual(len(list(self.wrappers.iterdir())), 6)
        retry = self.call("--install")
        self.assertNotEqual(retry.returncode, 0)
        self.assertIn("refuses duplicate schedules", retry.stderr)
        self.assertEqual(len(self.jobs()), 2)

    def run_wrapper(self):
        output = io.StringIO()
        calls = []
        def capture(executable, args):
            calls.append((executable, args, dict(os.environ)))
        with patch.dict(os.environ, {}, clear=True), patch("os.execv", side_effect=capture), \
                patch("os.chdir"), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            runpy.run_path(str(self.wrappers / "disanchor-gtm-telegram.py"))
        self.assertEqual(output.getvalue(), "")
        return calls

    def test_telegram_secrets_are_literal_allowlisted_and_never_printed(self):
        result = self.install()
        secret = self.repo / "state" / "runtime.env"
        secret.parent.mkdir()
        literal = 'fixture-token-$(echo no-shell)-`echo no-shell`=suffix'
        secret.write_text("TELEGRAM_BOT_TOKEN=" + literal + "\nTELEGRAM_CHAT_ID=123\nTELEGRAM_USER_ID=123\n")
        secret.chmod(0o600)
        calls = self.run_wrapper()
        self.assertEqual(len(calls), 1)
        executable, args, env = calls[0]
        self.assertEqual(executable, sys.executable)
        canonical_repo = self.repo.resolve()
        self.assertEqual(args, [sys.executable, "-m", "gtm", "--config", str(canonical_repo / "config.toml"),
                                "--state", str(canonical_repo / "state" / "gtm.sqlite"),
                                "--output", str(canonical_repo / "reports"), "telegram"])
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], literal)
        self.assertNotIn(literal, result.stdout + result.stderr)
        self.assertNotIn(literal, (self.wrappers / "disanchor-gtm-telegram.py").read_text())

    def test_wrapper_rejects_unsafe_file_and_unknown_keys_without_secret_echo(self):
        self.install()
        secret = self.repo / "state" / "runtime.env"
        secret.parent.mkdir()
        for body, mode in (("TELEGRAM_BOT_TOKEN=fixture-secret\n", 0o644),
                           ("UNKNOWN=fixture-secret\n", 0o600),
                           ("fixture-secret-without-equals\n", 0o600)):
            with self.subTest(mode=mode, malformed=body.startswith("fixture")):
                secret.write_text(body)
                secret.chmod(mode)
                with self.assertRaises(SystemExit) as caught:
                    self.run_wrapper()
                self.assertNotIn("fixture-secret", str(caught.exception))
        secret.unlink()
        secret.symlink_to(self.root / "absent")
        with self.assertRaises(SystemExit):
            self.run_wrapper()


if __name__ == "__main__":
    unittest.main()
