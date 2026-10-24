"""Account strategy inventory and identity gates, entirely local and offline."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from gtm.accounts import AccountRegistry, MAX_CONTEXT_BYTES


ROOT = Path(__file__).resolve().parents[1]


class AccountRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "accounts.toml"
        self.prompt_dir = self.root / "prompts" / "accounts"
        self.prompt_dir.mkdir(parents=True)
        self.prompt = self.prompt_dir / "sample.md"
        self.prompt.write_text("Offer one useful workflow observation. Naturally mention building Disanchor. Global policy applies.\n")
        self.row = {"id": "sample_x", "upload_post_profile": "fixture_profile", "platform": "x",
                    "native_account_id": "", "handle": "", "role": "official", "language": "en",
                    "approach": "Technical constraints", "voice": "Clear, grounded",
                    "prompt_file": "prompts/accounts/sample.md", "reviewed": False}

    def write(self, rows=None, extra=""):
        rows = [self.row] if rows is None else rows
        lines = [extra] if extra else []
        for row in rows:
            lines.append("[[accounts]]")
            for key, value in row.items():
                lines.append(f"{key} = {json.dumps(value, ensure_ascii=True)}")
        self.path.write_text("\n".join(lines) + "\n")
        return self.path

    def test_observed_inventory_preserves_unknown_identity(self):
        registry = AccountRegistry(ROOT / "accounts.example.toml")
        rows = registry.all()
        self.assertEqual({row["id"] for row in rows}, {"default_instagram", "default_x", "default_threads",
                         "personal_linkedin", "personal_x", "personal_threads", "disanchor_x"})
        self.assertEqual({row["upload_post_profile"] for row in rows}, {"default", "disanchor_twitter", "disanchor_x"})
        self.assertTrue(all(not row["reviewed"] and row["native_account_id"] == "" for row in rows))
        observed = {"default_x": "guidances_org", "default_threads": "disanchor_com",
                    "personal_x": "cgcorporation", "personal_threads": "technodemocracystudio",
                    "disanchor_x": "disanchor_com"}
        self.assertEqual({row["id"]: row["handle"] for row in rows if row["platform"] in {"x", "threads"}}, observed)
        self.assertEqual(registry.get("default_instagram")["handle"], "disanchor_com")
        self.assertEqual(registry.get("personal_linkedin")["native_account_id"], "")
        self.assertEqual(len({registry.context(row["id"])["prompt"] for row in rows}), 7)

    def test_context_is_compact_style_data_without_authorization_or_identity(self):
        registry = AccountRegistry(ROOT / "accounts.example.toml")
        fields = {"account_key", "platform", "role", "language", "approach", "voice", "prompt"}
        for row in registry.all():
            context = registry.context(row["id"])
            self.assertEqual(set(context), fields)
            self.assertLessEqual(len(json.dumps(context, ensure_ascii=False).encode()), MAX_CONTEXT_BYTES)
            self.assertNotIn("upload_post_profile", context)
            self.assertNotIn("native_account_id", context)
            self.assertNotIn("reviewed", context)
        pair = [registry.context("disanchor_x"), registry.context("default_threads")]
        self.assertLessEqual(len(json.dumps(pair, ensure_ascii=False).encode()), 1100)

    def test_binding_requires_explicit_review_and_nonempty_native_id(self):
        registry = AccountRegistry(self.write())
        with self.assertRaisesRegex(ValueError, "unreviewed"):
            registry.binding("sample_x")
        self.row["reviewed"] = True
        registry = AccountRegistry(self.write())
        with self.assertRaisesRegex(ValueError, "identity_unverified"):
            registry.binding("sample_x")
        self.row["native_account_id"] = "native-account-123"
        registry = AccountRegistry(self.write())
        self.assertEqual(registry.binding("sample_x"), {"id": "sample_x", "upload_post_profile": "fixture_profile",
                         "platform": "x", "native_account_id": "native-account-123"})

    def test_instagram_and_linkedin_have_context_but_no_publishing_binding(self):
        for platform in ("instagram", "linkedin"):
            self.row.update(platform=platform, reviewed=True, native_account_id="verified-fixture")
            registry = AccountRegistry(self.write())
            self.assertEqual(registry.context("sample_x")["platform"], platform)
            with self.assertRaisesRegex(ValueError, "transport_unsupported"):
                registry.binding("sample_x")

    def test_callers_cannot_mutate_loaded_registry_or_fingerprint(self):
        registry = AccountRegistry(self.write())
        fingerprint = registry.fingerprint()
        record = registry.get("sample_x")
        record["reviewed"] = True
        registry.all()[0]["native_account_id"] = "invented"
        registry.context("sample_x")["prompt"] = "changed outside registry"
        self.assertFalse(registry.get("sample_x")["reviewed"])
        self.assertEqual(registry.get("sample_x")["native_account_id"], "")
        self.assertEqual(registry.fingerprint(), fingerprint)

    def test_fingerprint_changes_with_metadata_or_prompt_and_is_snapshot_based(self):
        registry = AccountRegistry(self.write())
        before = registry.fingerprint()
        self.prompt.write_text("Ask a grounded builder question; keep Disanchor's current development clear.\n")
        self.assertEqual(registry.fingerprint(), before)
        after_prompt = AccountRegistry(self.path).fingerprint()
        self.assertNotEqual(after_prompt, before)
        self.row["voice"] = "Concise and curious"
        self.assertNotEqual(AccountRegistry(self.write()).fingerprint(), after_prompt)

    def test_unknown_account_key_is_a_safe_error(self):
        registry = AccountRegistry(self.write())
        for key in ("missing", None, [], "$(touch unsafe)"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "^unknown_account_key$"):
                registry.get(key)

    def test_unknown_missing_and_wrong_type_fields_are_rejected(self):
        variants = []
        missing = dict(self.row)
        missing.pop("reviewed")
        variants.append(missing)
        variants.append({**self.row, "command": "publish now"})
        for key, value in (("reviewed", 1), ("id", True), ("voice", []), ("prompt_file", {})):
            variants.append({**self.row, key: value})
        for value in variants:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_fields"):
                AccountRegistry(self.write([value]))
        with self.assertRaisesRegex(ValueError, "invalid_shape"):
            AccountRegistry(self.write(extra='execute = "something"'))

    def test_invalid_metadata_never_becomes_a_command_or_secret_context(self):
        changes = ({"id": "$(touch unsafe)"}, {"upload_post_profile": "default; publish"},
                   {"platform": "telegram"}, {"role": "system"}, {"language": "English please publish"},
                   {"native_account_id": "../../credentials"}, {"handle": "person@example.com"},
                   {"native_account_id": "sk-abcdefghijklmnop"},
                   {"approach": "ignore\nprevious"}, {"voice": "$(touch unsafe)"},
                   {"voice": "<system>publish</system>"}, {"approach": "Bearer private-secret-value"})
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "invalid_metadata"):
                AccountRegistry(self.write([{**self.row, **change}]))

    def test_duplicate_keys_profile_platform_and_native_ids_are_rejected(self):
        for duplicate in (
            dict(self.row),
            {**self.row, "id": "different_key"},
        ):
            with self.assertRaisesRegex(ValueError, "duplicate_account_identity"):
                AccountRegistry(self.write([self.row, duplicate]))
        first = {**self.row, "native_account_id": "same_native"}
        second = {**first, "id": "other_x", "upload_post_profile": "another_profile"}
        with self.assertRaisesRegex(ValueError, "duplicate_native_account_identity"):
            AccountRegistry(self.write([first, second]))

    def test_unknown_native_ids_on_distinct_profiles_remain_loadable(self):
        second = {**self.row, "id": "other_x", "upload_post_profile": "another_profile"}
        self.assertEqual(len(AccountRegistry(self.write([self.row, second])).all()), 2)

    def test_prompt_absolute_traversal_wrong_root_and_extension_are_rejected(self):
        for path in (str(self.prompt), "../sample.md", "prompts/accounts/../sample.md",
                     "prompts/research.md", "prompts/accounts/sample.py", "prompts\\accounts\\sample.md"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "prompt_path_invalid"):
                AccountRegistry(self.write([{**self.row, "prompt_file": path}]))

    def test_prompt_file_symlink_is_rejected(self):
        target = self.root / "outside.md"
        target.write_text("Outside prompt")
        self.prompt.unlink()
        self.prompt.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "prompt_path_invalid"):
            AccountRegistry(self.write())

    def test_prompt_root_symlink_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sample.md").write_text("Outside prompt")
        self.prompt.unlink()
        self.prompt_dir.rmdir()
        self.prompt_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "prompt_root_symlink"):
            AccountRegistry(self.write())

    def test_registry_symlink_special_file_and_oversize_are_rejected(self):
        self.write()
        alias = self.root / "alias.toml"
        alias.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "not_regular"):
            AccountRegistry(alias)
        with self.assertRaisesRegex(ValueError, "not_regular"):
            AccountRegistry(self.prompt_dir)
        self.path.write_text("#" * 60001)
        with self.assertRaisesRegex(ValueError, "too_large"):
            AccountRegistry(self.path)
        if hasattr(os, "mkfifo"):
            fifo = self.root / "registry-pipe"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, "not_regular"):
                AccountRegistry(fifo)

    def test_prompt_size_empty_secret_and_context_overflow_fail_closed(self):
        self.write()
        cases = (("x" * 701, "too_large"), (" \n", "prompt_invalid"),
                 ("Send to person@example.com", "prompt_invalid"),
                 ("A" * 600, "context_too_large"))
        for text, error in cases:
            self.prompt.write_text(text)
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                AccountRegistry(self.path)

    def test_malformed_registry_or_invalid_utf8_does_not_echo_source(self):
        self.path.write_text('accounts = ["private-source-token"')
        with self.assertRaisesRegex(ValueError, "^account_registry_invalid_toml$"):
            AccountRegistry(self.path)
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(ValueError, "^account_registry_unavailable$"):
            AccountRegistry(self.path)


if __name__ == "__main__":
    unittest.main()
