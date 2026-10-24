"""User-authorized spending and bounded read-only feedback configuration."""
from pathlib import Path
import tempfile
import unittest

from gtm.config import Config


class FeedbackConfigTests(unittest.TestCase):
    def test_default_and_committed_config_use_twenty_usd_with_unchanged_model_allocation(self):
        for config in (Config(), Config.load(Path(__file__).resolve().parents[1] / "config.toml")):
            config.validate()
            self.assertEqual(config.budget.target_total_usd, 20)
            self.assertEqual(config.budget.model_budget_usd, 5)
            self.assertEqual(config.budget.target_total_usd * config.budget.warning_threshold, 14)
            self.assertEqual(config.budget.target_total_usd * config.budget.stop_optional_work_threshold, 18)
            self.assertFalse(config.routing.enabled)
            self.assertFalse(config.budget.billing_verified)
            self.assertTrue(config.feedback.enabled)
            self.assertTrue(config.learning.enabled)

    def test_feedback_limits_reject_unbounded_or_weak_evidence_configuration(self):
        invalid = {"max_posts_per_collection": (0, 11), "max_comments_per_post": (0, 41),
                   "max_comment_pages": (0, 3), "minimum_posts": (2, 11),
                   "minimum_exposures": (99, 1_000_001), "minimum_comments": (4, 41)}
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    config = Config()
                    setattr(config.feedback, field, value)
                    config.validate()

    def test_feedback_can_reduce_collection_and_raise_evidence_threshold(self):
        config = Config()
        config.feedback.max_posts_per_collection = 1
        config.feedback.max_comments_per_post = 5
        config.feedback.max_comment_pages = 1
        config.feedback.minimum_posts = 10
        config.feedback.minimum_exposures = 1_000_000
        config.feedback.minimum_comments = 40
        config.validate()

    def test_feedback_numeric_fields_reject_boolean_fractional_and_string_values(self):
        for field in ("max_posts_per_collection", "max_comments_per_post", "max_comment_pages",
                      "minimum_posts", "minimum_exposures", "minimum_comments"):
            for value in (True, 3.5, "3", None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    config = Config()
                    setattr(config.feedback, field, value)
                    config.validate()

    def test_native_skill_paths_must_be_explicit_absolute_pair(self):
        for source, interpreter in (("/srv/hermes", ""), ("", "/srv/hermes/venv/bin/python"),
                                    ("relative/hermes", "/srv/hermes/python"),
                                    ("/srv/hermes", "python"), ("~/hermes", "~/hermes/python")):
            with self.subTest(source=source, interpreter=interpreter), self.assertRaises(ValueError):
                config = Config()
                config.learning.hermes_source_dir = source
                config.learning.hermes_python = interpreter
                config.validate()
        config = Config()
        config.learning.hermes_source_dir = "/srv/hermes-agent"
        config.learning.hermes_python = "/srv/hermes-agent/venv/bin/python"
        config.validate()  # existence/capability is independently checked before native execution

    def test_empty_native_paths_and_disabled_feedback_are_valid_without_enabling_model_work(self):
        config = Config()
        config.feedback.enabled = False
        config.learning.enabled = False
        config.validate()
        self.assertEqual(config.learning.hermes_source_dir, "")
        self.assertEqual(config.learning.hermes_python, "")
        self.assertFalse(config.routing.enabled)
        self.assertFalse(config.publishing.auto_publish)

    def test_learning_feedback_controls_reject_nonboolean_flags(self):
        for section in ("learning", "feedback"):
            for value in (1, "true", None):
                with self.subTest(section=section, value=value), self.assertRaises(ValueError):
                    config = Config()
                    getattr(config, section).enabled = value
                    config.validate()

    def test_feedback_learning_settings_load_and_change_approval_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('[feedback]\nmax_comment_pages=1\nminimum_posts=4\n[learning]\nenabled=false\n')
            config = Config.load(path)
        self.assertEqual(config.feedback.max_comment_pages, 1)
        self.assertEqual(config.feedback.minimum_posts, 4)
        self.assertFalse(config.learning.enabled)
        self.assertNotEqual(config.fingerprint(), Config().fingerprint())

    def test_budget_rejects_nonfinite_nonpositive_or_boolean_total(self):
        for value in (0, -20, float("inf"), float("nan"), True, "20"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config = Config()
                config.budget.target_total_usd = value
                config.validate()

    def test_budget_cannot_exceed_authorized_total_or_category_caps(self):
        for field, value in (("target_total_usd", 20.01), ("model_budget_usd", 5.01),
                             ("external_tools_budget_usd", 2.01)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "ceiling"):
                config = Config()
                setattr(config.budget, field, value)
                config.validate()

    def test_budget_can_be_lowered_without_relaxing_guards(self):
        config = Config()
        config.budget.target_total_usd = 10
        config.budget.model_budget_usd = 3
        config.budget.external_tools_budget_usd = 1
        config.validate()
        self.assertEqual(config.budget.target_total_usd * config.budget.warning_threshold, 7)
        self.assertEqual(config.budget.target_total_usd * config.budget.stop_optional_work_threshold, 9)
        self.assertFalse(config.publishing.auto_publish)
        self.assertFalse(config.routing.billing_cap_verified)


if __name__ == "__main__":
    unittest.main()
