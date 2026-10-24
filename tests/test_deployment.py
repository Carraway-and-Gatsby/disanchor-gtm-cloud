import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


PROJECT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), PROJECT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load_script("package-cloud")
checks = package.checks


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cloud-bundle-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        for relative, data in checks.source_entries(PROJECT).items():
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        self.output = Path(self.temp.name) / "deploy.zip"

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        return path

    def test_preflight_is_offline_read_only_and_does_not_claim_authentication(self):
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("process forbidden")):
            result = checks.preflight(self.root)
        after = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["status"], "offline_checks_passed")
        self.assertFalse(result["credentials_verified"])
        self.assertFalse(result["cloud_deployed"])
        self.assertEqual((result["http_calls"], result["model_calls"], result["cron_mutations"]), (0, 0, 0))

    def test_optional_runtime_check_only_reads_version_and_cron_help(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            stdout = "Hermes fixture runtime\n" if args[-1] == "--version" else "--no-agent --script --deliver --name --repeat --workdir"
            return subprocess.CompletedProcess(args, 0, stdout, "")
        with patch("gtm.process.bounded_run", side_effect=runner):
            result = checks.preflight(self.root, runtime_check=True)
        self.assertEqual(calls, [["hermes", "--version"], ["hermes", "cron", "create", "--help"]])
        self.assertTrue(result["hermes_runtime"]["checked"])
        self.assertFalse(result["hermes_runtime"]["model_compatibility_verified"])
        self.assertNotIn("Hermes fixture runtime", json.dumps(result))

    def test_fresh_cli_preflight_creates_no_bytecode_or_state_files(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        result = subprocess.run([sys.executable, str(self.root / "scripts/cloud-preflight.py")],
                                cwd=self.root, env={"PATH": os.defpath, "HOME": str(self.root)},
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")))

    def test_bundle_is_reproducible_and_manifest_verifies_every_entry(self):
        first = package.build_bundle(self.root, self.output)
        original = self.output.read_bytes()
        for path in self.root.rglob("*"):
            if path.is_file():
                os.utime(path, (1700000000, 1700000000))
        second = package.build_bundle(self.root, self.output)
        self.assertEqual(original, self.output.read_bytes())
        self.assertEqual(first["sha256"], second["sha256"])
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(set(names)))
            manifest = json.loads(archive.read("bundle-manifest.json"))
            self.assertEqual({row["path"] for row in manifest["files"]}, set(names) - {"bundle-manifest.json"})
            for row in manifest["files"]:
                data = archive.read(row["path"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
                self.assertEqual(len(data), row["size"])
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))

    def test_runtime_secrets_caches_reports_and_local_accounts_are_excluded(self):
        marker = "excluded-fixture-" + hashlib.sha256(str(self.root).encode()).hexdigest()
        excluded = (".env", ".env.production", "accounts.toml", "accounts.local.toml", "state/runtime.env", "state/live.sqlite",
                    "reports/simulation.sqlite", "reports/private.html", "gtm/__pycache__/cached.pyc",
                    "scripts/credentials.json", "scripts/private-key.txt", "docs/secrets.toml", "docs/.env", ".git/config")
        for relative in excluded:
            self.write(relative, marker)
        (self.root / "state/runtime.env").chmod(0o600)
        package.build_bundle(self.root, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertTrue(set(excluded).isdisjoint(archive.namelist()))
            self.assertIn(".env.example", archive.namelist())
            self.assertFalse(any(marker.encode() in archive.read(name) for name in archive.namelist()), "Excluded fixture content appeared")

    def test_nonempty_env_example_and_credential_signatures_block_packaging(self):
        original = (self.root / ".env.example").read_text()
        self.write(".env.example", "UPLOAD_POST_API_KEY=fixture-private-value\n")
        with self.assertRaises(checks.ValidationError) as caught:
            package.build_bundle(self.root, self.output)
        self.assertNotIn("fixture-private-value", str(caught.exception))
        self.assertFalse(self.output.exists())
        self.write(".env.example", original)
        self.write("docs/accidental.md", "token: " + "ghp_" + "a" * 30)
        with self.assertRaises(checks.ValidationError) as caught:
            package.build_bundle(self.root, self.output)
        self.assertNotIn("a" * 30, str(caught.exception))

    def test_hardcoded_python_credentials_and_env_fallbacks_are_blocked(self):
        cases = [
            'UPLOAD_POST_API_KEY = "opaque-production-literal"',
            'api_key: str = "opaque-production-literal"',
            'self._token = "opaque-production-literal"',
            'os.environ["CLONE_APP_KEY"] = "opaque-production-literal"',
            'settings = {"password": "opaque-production-literal"}',
            'client(api_key="opaque-production-literal")',
            'def client(api_key="opaque-production-literal"): pass',
            'API_KEY = "opaque-" + "production-literal"',
            'API_KEY = os.getenv("UPLOAD_POST_API_KEY", "opaque-production-literal")',
            'API_KEY = os.environ.get("UPLOAD_POST_API_KEY", "opaque-production-literal")',
        ]
        for source in cases:
            with self.subTest(source=source.split("=")[0]), self.assertRaises(checks.ValidationError) as caught:
                checks._scan_source("gtm/accidental.py", source.encode())
            self.assertNotIn("opaque-production-literal", str(caught.exception))

    def test_hardcoded_javascript_shell_and_document_literals_are_blocked(self):
        cases = [
            ("web/accidental.js", 'const apiKey = "opaque-production-literal";'),
            ("web/accidental.js", 'const settings = {"APIKey": "opaque-production-literal"};'),
            ("web/accidental.js", 'const API_KEY = process.env.API_KEY || "opaque-production-literal";'),
            ("scripts/accidental.sh", 'export UPLOAD_POST_API_KEY=opaque-production-literal'),
            ("scripts/accidental.sh", "UPLOAD_POST_API_KEY='opaque-production-literal'"),
            ("scripts/accidental.sh", 'UPLOAD_POST_API_KEY="${UPLOAD_POST_API_KEY:-opaque-production-literal}"'),
            ("docs/accidental.yml", 'UPLOAD_POST_API_KEY: opaque-production-literal'),
            ("docs/accidental.yaml", 'api_key: "opaque-production-literal"'),
            ("docs/accidental.md", '{"api_key": "opaque-production-literal"}'),
            ("docs/accidental.toml", 'upload_post_api_key = "opaque-production-literal"'),
            (".github/workflows/accidental.yml", '      TOKEN: opaque-production-literal'),
        ]
        for filename, source in cases:
            with self.subTest(filename=filename), self.assertRaises(checks.ValidationError) as caught:
                checks._scan_source(filename, source.encode())
            self.assertNotIn("opaque-production-literal", str(caught.exception))

    def test_runtime_lookups_empty_values_and_explicit_placeholders_are_preserved(self):
        cases = [
            ("gtm/example.py", 'api_key = os.getenv("UPLOAD_POST_API_KEY", "")'),
            ("gtm/example.py", 'api_key = os.environ["UPLOAD_POST_API_KEY"]'),
            ("gtm/example.py", 'self._token = token'),
            ("gtm/example.py", 'client(api_key=None)'),
            ("gtm/example.py", 'settings = {"api_key": "<secret>"}'),
            ("web/example.js", 'const apiKey = process.env.UPLOAD_POST_API_KEY;'),
            ("web/example.js", "const apiKey = process.env['UPLOAD_POST_API_KEY'];"),
            ("scripts/example.sh", 'export UPLOAD_POST_API_KEY="$UPLOAD_POST_API_KEY"'),
            ("scripts/example.sh", 'UPLOAD_POST_API_KEY=${UPLOAD_POST_API_KEY}'),
            ("docs/example.yml", 'api_key: <secret>'),
            ("docs/example.md", '{"api_key": "<secret>"}'),
            (".github/workflows/example.yml", '      TOKEN: ${{ secrets.TEST_TOKEN }}'),
            (".env.example", 'UPLOAD_POST_API_KEY=\nTELEGRAM_BOT_TOKEN=\n'),
        ]
        for filename, source in cases:
            with self.subTest(filename=filename):
                checks._scan_source(filename, source.encode())

    def test_mock_exception_is_scoped_and_unknown_test_literals_still_blocked(self):
        fixture = 'api_key = "fixture-not-a-real-credential"'
        checks._scan_source("tests/test_example.py", fixture.encode())
        with self.assertRaises(checks.ValidationError):
            checks._scan_source("gtm/example.py", fixture.encode())
        with self.assertRaises(checks.ValidationError):
            checks._scan_source("tests/test_example.py", b'api_key = "unmarked-opaque-production-literal"')

    def test_jwt_shape_is_blocked_without_printing_or_real_credentials(self):
        synthetic = "eyJ" + "a" * 25 + "." + "b" * 30 + "." + "c" * 30
        with self.assertRaises(checks.ValidationError) as caught:
            checks._scan_source("docs/accidental.md", synthetic.encode())
        self.assertNotIn(synthetic, str(caught.exception))

    def test_literal_scan_blocks_bundle_before_artifact_is_written(self):
        self.write("docs/accidental.yml", "UPLOAD_POST_API_KEY: opaque-production-literal\n")
        with self.assertRaises(checks.ValidationError):
            package.build_bundle(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_live_defaults_missing_files_and_source_symlinks_are_refused(self):
        config = self.root / "config.toml"
        original = config.read_text()
        config.write_text(original.replace("auto_publish = false", "auto_publish = true", 1))
        with self.assertRaises(checks.ValidationError):
            checks.preflight(self.root)
        config.write_text(original)
        (self.root / "gtm/__main__.py").unlink()
        with self.assertRaises(checks.ValidationError):
            checks.preflight(self.root)
        (self.root / "gtm/__main__.py").symlink_to(PROJECT / "gtm/__main__.py")
        with self.assertRaises(checks.ValidationError):
            checks.preflight(self.root)

    def test_runtime_env_permission_check_never_reads_its_contents(self):
        secret = self.write("state/runtime.env", "fixture-private-content")
        secret.chmod(0o644)
        with self.assertRaises(checks.ValidationError):
            checks.preflight(self.root)
        secret.chmod(0o600)
        result = checks.preflight(self.root)
        self.assertEqual(result["local_paths"]["runtime_env"], "private_file_present_contents_not_read")
        self.assertNotIn("fixture-private-content", json.dumps(result))

    def test_unpacked_bundle_compiles_and_runs_representative_offline_tests(self):
        package.build_bundle(self.root, self.output)
        unpacked = Path(self.temp.name) / "unpacked"
        with zipfile.ZipFile(self.output) as archive:
            archive.extractall(unpacked)
        for path in unpacked.rglob("*.py"):
            py_compile.compile(str(path), doraise=True)
        self.assertEqual(len(list((unpacked / "tests").glob("test_*.py"))), len(list((self.root / "tests").glob("test_*.py"))))
        # Do not recurse into this packaging test from its own unpacked copy.
        env = {"PATH": os.defpath, "HOME": str(Path(self.temp.name) / "isolated-home")}
        for pattern in ("test_core.py", "test_cloud_install.py", "test_process.py"):
            result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern],
                                    cwd=unpacked, env=env, capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, "Unpacked suite failed: " + pattern + "\n" + result.stderr)


if __name__ == "__main__":
    unittest.main()
