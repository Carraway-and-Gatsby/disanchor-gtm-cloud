import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gtm.process import MAX_OUTPUT_BYTES, bounded_run, child_environment


@unittest.skipUnless(os.name == "posix", "Cloud subprocess isolation requires POSIX")
class BoundedProcessTests(unittest.TestCase):
    def run_python(self, source, **kwargs):
        return bounded_run([sys.executable, "-c", source], **kwargs)

    def test_text_input_and_both_streams_return_completed_process_without_shell(self):
        literal = "한글 $(echo no-shell) `echo no-shell`"
        result = self.run_python("import sys; print(sys.stdin.read(), end=''); print('diagnostic', file=sys.stderr)", input=literal)
        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, literal)
        self.assertEqual(result.stderr, "diagnostic\n")

    def test_concurrent_pipe_draining_avoids_input_output_deadlock(self):
        result = self.run_python("import sys; sys.stdout.write('a'*32768); sys.stdout.flush(); data=sys.stdin.read(); sys.stderr.write(str(len(data)))", input="x" * (512 * 1024), timeout=5)
        self.assertEqual(result.stdout, "a" * 32768)
        self.assertEqual(result.stderr, str(512 * 1024))

    def test_nonzero_return_and_safe_check_exception(self):
        source = "import sys; print('fixture-secret'); print('stderr-secret', file=sys.stderr); sys.exit(7)"
        result = self.run_python(source, check=False)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "fixture-secret\n")
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.run_python(source, check=True)
        self.assertEqual(caught.exception.returncode, 7)
        self.assertIsNone(caught.exception.output)
        self.assertIsNone(caught.exception.stderr)
        self.assertNotIn("secret", str(caught.exception))

    def test_output_limit_is_aggregate_and_exact_boundary_is_accepted(self):
        result = self.run_python(f"import sys; sys.stdout.write('a'*{MAX_OUTPUT_BYTES // 2}); sys.stderr.write('b'*{MAX_OUTPUT_BYTES // 2})")
        self.assertEqual(len(result.stdout) + len(result.stderr), MAX_OUTPUT_BYTES)
        with self.assertRaises(subprocess.SubprocessError) as caught:
            self.run_python(f"import sys; sys.stdout.write('a'*{MAX_OUTPUT_BYTES // 2}); sys.stderr.write('fixture-secret'+'b'*{MAX_OUTPUT_BYTES // 2})")
        self.assertEqual(str(caught.exception), "bounded_process_output_limit_exceeded")

    def test_deadline_omits_captured_secrets_and_leaves_no_threads(self):
        threads = set(threading.enumerate())
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.run_python("import sys,time; print('fixture-secret',flush=True); time.sleep(10)", timeout=.2)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNone(caught.exception.output)
        self.assertIsNone(caught.exception.stderr)
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.assertEqual(set(threading.enumerate()), threads)

    def test_deadline_applies_when_child_closes_all_output_but_keeps_running(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_python("import os,time; os.close(1); os.close(2); time.sleep(10)", timeout=.2)

    def check_descendants_terminated(self, overflow):
        with tempfile.TemporaryDirectory() as folder:
            ready, marker = Path(folder) / "ready", Path(folder) / "should-not-exist"
            child = f"import pathlib,time; pathlib.Path({str(ready)!r}).write_text('ready'); time.sleep(.7); pathlib.Path({str(marker)!r}).write_text('survived')"
            parent = ("import pathlib,subprocess,sys,time; "
                      f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
                      f"ready=pathlib.Path({str(ready)!r}); "
                      "\nwhile not ready.exists(): time.sleep(.005)\n")
            if overflow:
                parent += f"sys.stdout.write('x'*{MAX_OUTPUT_BYTES + 1}); sys.stdout.flush(); time.sleep(10)"
            else:
                # The group leader exits while its child keeps inherited pipes.
                parent += "sys.exit(0)"
            with self.assertRaises(subprocess.SubprocessError):
                self.run_python(parent, timeout=.3)
            self.assertTrue(ready.exists(), "descendant must have started before termination")
            time.sleep(.8)
            self.assertFalse(marker.exists(), "descendant survived process-group termination")

    def test_timeout_kills_descendants_even_when_group_leader_already_exited(self):
        self.check_descendants_terminated(overflow=False)

    def test_overflow_kills_descendants(self):
        self.check_descendants_terminated(overflow=True)

    def test_invalid_utf8_and_invalid_arguments_fail_without_raw_output(self):
        with self.assertRaises(subprocess.SubprocessError) as caught:
            self.run_python("import os; os.write(1,b'fixture-secret\\xff')")
        self.assertEqual(str(caught.exception), "bounded_process_invalid_utf8")
        for kwargs in ({"timeout": 0}, {"timeout": float("inf")}, {"text": False}, {"capture_output": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_python("raise AssertionError('should not spawn')", **kwargs)
        with self.assertRaises(TypeError):
            self.run_python("raise AssertionError('should not spawn')", input=b"bytes")

    def test_parent_social_analytics_and_shell_credentials_are_absent_at_child_startup(self):
        denied = {key: "offline-fixture" for key in ("UPLOAD_POST_API_KEY", "TELEGRAM_BOT_TOKEN", "CLONE_APP_KEY",
                  "GA4_ACCESS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "OPENROUTER_API_KEY", "GITHUB_TOKEN",
                  "PYTHONPATH", "PYTHONSTARTUP", "BASH_ENV", "HERMES_PROFILE", "HERMES_CONFIG", "HERMES_ENV",
                  "HTTP_PROXY", "HTTPS_PROXY")}
        with patch.dict(os.environ, denied, clear=True):
            result = self.run_python("import os; print(sorted(os.environ))")
        for key in denied:
            self.assertNotIn(key, result.stdout)
        self.assertIn("PATH", result.stdout)
        self.assertNotIn("offline-fixture", result.stdout)

    def test_model_environment_keeps_only_selected_provider_and_runtime_profile(self):
        source = {"HOME": "/home/fixture", "HERMES_HOME": "/home/fixture/hermes", "PATH": os.defpath,
                  "OPENROUTER_API_KEY": "offline-provider-fixture", "UPLOAD_POST_API_KEY": "offline-social-fixture",
                  "TELEGRAM_BOT_TOKEN": "offline-chat-fixture", "ANTHROPIC_API_KEY": "offline-other-fixture",
                  "HERMES_PROFILE": "unexpected-profile", "PYTHONPATH": "/untrusted/import"}
        with patch.dict(os.environ, source, clear=True):
            provider = child_environment(hermes=True, provider="openrouter")
            preflight = child_environment(hermes=True)
        self.assertEqual(provider["OPENROUTER_API_KEY"], "offline-provider-fixture")
        self.assertEqual(provider["HERMES_HOME"], source["HERMES_HOME"])
        self.assertEqual(provider["HOME"], source["HOME"])
        for key in ("UPLOAD_POST_API_KEY", "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "HERMES_PROFILE", "PYTHONPATH"):
            self.assertNotIn(key, provider)
        self.assertNotIn("OPENROUTER_API_KEY", preflight)

    def test_explicit_environment_cannot_reintroduce_forbidden_credentials(self):
        for env in ({"UPLOAD_POST_API_KEY": "offline-fixture"}, {"PATH": "/bin\x00bad"}, {"PATH": 3}, []):
            with self.subTest(env=env), patch("gtm.process.subprocess.Popen") as spawn:
                with self.assertRaisesRegex(ValueError, "environment_not_allowlisted"):
                    self.run_python("raise AssertionError('must not run')", env=env)
                spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
