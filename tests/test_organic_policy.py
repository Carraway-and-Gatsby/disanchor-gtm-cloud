"""Natural development context and conversation-only publishing boundaries."""
import unittest

from gtm.config import Config, timestamp, slots
from gtm.inference import validate_draft, MockInference
from gtm.policy import DEVELOPMENT_DISCLOSURE, POLICY_VERSION, comparison_body, seed_text, validate_content


class OrganicPolicyTests(unittest.TestCase):
    body = "Which part of your work setup takes the longest to rebuild on a new computer?"

    def test_all_ten_candidates_weave_in_development_without_footer_or_sales_cta(self):
        c = Config()
        previous = []
        self.assertEqual(POLICY_VERSION, "2026-10-25.2")
        for slot in slots(c, timestamp("2026-10-24T12:00:00Z")):
            with self.subTest(slot=slot["id"]):
                post = seed_text(slot, c)
                self.assertEqual(post["cta_type"], "none")
                self.assertEqual(post["variant"], "organic-v3")
                self.assertRegex(post["text"], DEVELOPMENT_DISCLOSURE)
                self.assertNotIn("\n\n", post["text"])
                self.assertNotRegex(post["text"], r"Disanchor (?:is (?:still )?in development|remains in development|is still being developed)\.$")
                self.assertTrue(validate_content(post["platform"], post["text"], previous))
                previous.append(post["text"])

    def test_present_development_is_recognized_in_ordinary_sentences(self):
        examples = (
            "We're still building Disanchor, and " + self.body,
            "I'm developing Disanchor, so " + self.body,
            "While we are currently building Disanchor, " + self.body,
            "As we develop Disanchor ahead of release, " + self.body,
            "As we build Disanchor before its first release, " + self.body,
            "For Disanchor, still in development, " + self.body,
            "During Disanchor's ongoing development, " + self.body,
            "Disanchor isn't released yet, so " + self.body,
            "Disanchor has not been launched yet. " + self.body,
            "For the not-yet-released Disanchor, " + self.body,
            "One question for Disanchor while it's still in development: " + self.body,
            "Which part of your work setup, while we're building Disanchor, takes the longest to rebuild on a new computer?",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(validate_content("x", text))

    def test_curly_apostrophes_do_not_break_natural_development_context(self):
        for text in ("We’re still building Disanchor, and " + self.body,
                     "Disanchor isn’t released yet, so " + self.body,
                     "For Disanchor’s current development, " + self.body):
            with self.subTest(text=text):
                self.assertTrue(validate_content("x", text))

    def test_disclosure_is_required_even_without_a_product_pitch(self):
        for suffix in ("", " Disanchor is exploring this.", " Disanchor is validating persistence.",
                       " We are researching Disanchor.", " We were building Disanchor.",
                       " Disanchor was in development.", " Disanchor is no longer in development.",
                       " We are building with Disanchor.", " We're building Disanchor integrations.",
                       " We're developing Disanchor's website.", " As we develop Disanchor features, this matters."):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "explicitly state"):
                validate_content("x", self.body + suffix)

    def test_disclosure_alone_is_not_a_post(self):
        for text in ("Disanchor is in development.", "We're still building Disanchor.",
                     "Disanchor isn't released yet.", "As we develop Disanchor ahead of release, hello."):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "substantive"):
                validate_content("x", text)

    def test_natural_disclosure_placement_cannot_hide_duplicate_body(self):
        old = self.body + " Disanchor is in development."
        variants = (
            "While we're still building Disanchor, " + self.body,
            "For Disanchor's ongoing development, " + self.body,
            "As we develop Disanchor ahead of release, " + self.body,
            "Disanchor isn't released yet. " + self.body,
            self.body + " Disanchor remains in development.",
            "Which part of your work setup, while we're building Disanchor, takes the longest to rebuild on a new computer?",
        )
        for new in variants:
            with self.subTest(text=new):
                validate_content("x", new)
                with self.assertRaisesRegex(ValueError, "similar"):
                    validate_content("x", new, [old])

    def test_normalization_ignores_disclosure_and_punctuation_but_keeps_content(self):
        first = "We're building Disanchor, and which application keeps your old computer useful?"
        second = "And which application keeps your old computer useful! Disanchor isn't released yet."
        self.assertEqual(comparison_body(first), comparison_body(second))
        with self.assertRaisesRegex(ValueError, "similar"):
            validate_content("x", second, [first])

    def test_shared_development_context_does_not_make_distinct_thoughts_duplicates(self):
        first = "We're building Disanchor, and which application keeps your old computer useful?"
        second = "We're building Disanchor, and a setup checklist should explain the order of installation as well as the software names."
        self.assertTrue(validate_content("x", second, [first]))

    def test_no_promotional_action_or_link_on_either_platform(self):
        suffixes = ("https://disanchor.com", "disanchor.com", "https://example.xyz", "Join our waitlist.",
                    "Sign up.", "Subscribe.", "Book a demo.", "Check our profile.", "Link in bio.",
                    "Visit our website.", "Get early access.", "Try Disanchor.", "Comment WINDOWS for access.")
        for platform in ("x", "threads"):
            for suffix in suffixes:
                with self.subTest(platform=platform, suffix=suffix), self.assertRaises(ValueError):
                    validate_content(platform, self.body + " Disanchor is in development. " + suffix)

    def test_inference_and_mock_use_same_disclosure_policy(self):
        base = {"platform": "threads", "text": "While we're building Disanchor, " + self.body,
                "hypothesis": "H2", "hook": "question", "cta": "none"}
        self.assertEqual(validate_draft(base), base)
        self.assertIsNone(validate_draft({**base, "text": self.body}))
        for cta in ("profile", "research_interest"):
            self.assertIsNone(validate_draft({**base, "cta": cta}))
        self.assertIsNotNone(validate_draft(MockInference().generate({})["draft"]))


if __name__ == "__main__":
    unittest.main()
