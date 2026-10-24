import copy
from datetime import datetime, timedelta, timezone
import json
import unittest
from urllib.parse import urlsplit, parse_qs

from gtm.feedback import (UploadPostFeedbackClient, aggregate_feedback,
                          classify_sentiment, sentiment_summary)
from gtm.publisher import ValidationError


NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
FINGERPRINT = "a" * 64


def binding(**changes):
    return {"post_id": "x_01", "account_key": "disanchor_x", "account_fingerprint": FINGERPRINT,
            "platform": "x", "profile_username": "disanchor_x", "platform_post_id": "10001",
            "native_account_id": "90001", **changes}


def comment(i, text="Great post", **changes):
    return {"id": str(i), "text": text, "user": {"id": str(50000 + i), "username": "private_person"}, **changes}


def identity_response(platform="x", native="90001", **changes):
    return {"status_code": 200, "headers": {}, "body": {"success": True,
            "profile": {"username": "disanchor_x", "social_accounts": {
                platform: {"username": native, "reauth_required": False, **changes}}}}}


def response(comments=None, **changes):
    return {"status_code": 200, "headers": {}, "body": {"success": True, "comments": comments or [], **changes}}


class Transport:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, *args):
        self.calls.append(args)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def metric(post_id="x_01", *, observed=NOW, metrics=None, **changes):
    return {"observed_at": observed.isoformat(), "source": "upload_post",
            "payload": {"post_id": post_id, "account_key": "disanchor_x", "account_fingerprint": FINGERPRINT,
                        "platform": "x", "hypothesis": "H1", "hook_type": "question",
                        "metrics": metrics if metrics is not None else {"impressions": 100, "likes": 10, "replies": 3, "reposts": 2},
                        **changes}}


def sentiment(post_id="x_01", *, observed=NOW, counts=None, **changes):
    return {"observed_at": observed.isoformat(), "source": "upload_post",
            "payload": {"post_id": post_id, "account_key": "disanchor_x", "account_fingerprint": FINGERPRINT,
                        "platform": "x", "status": "sampled", "partial": False,
                        "sentiment": sentiment_summary(counts or {"positive": 5, "negative": 0, "mixed": 0, "unknown": 0}),
                        **changes}}


class CommentClientTest(unittest.TestCase):
    def client(self, *responses, **kwargs):
        transport = Transport(identity_response(), *responses)
        return UploadPostFeedbackClient("test-key-private", [binding()], transport, **kwargs), transport

    def test_only_documented_get_and_known_own_post(self):
        client, transport = self.client(response([comment(1, "Useful check")]))
        result = client.collect("x_01")
        method, url, headers, body = transport.calls[1]
        self.assertEqual(method, "GET")
        self.assertEqual(urlsplit(url).path, "/api/uploadposts/comments")
        self.assertEqual(parse_qs(urlsplit(url).query), {"platform": ["x"], "user": ["disanchor_x"], "post_id": ["10001"], "limit": ["25"]})
        self.assertIsNone(body)
        self.assertEqual(result["sentiment"]["counts"]["positive"], 1)
        self.assertEqual(result["coverage"], "mentions_sample")
        self.assertEqual(result["sentiment"]["label"], "unknown")
        encoded = json.dumps(result)
        for secret in ("Useful check", "private_person", "50001", "test-key-private", "90001", "10001"):
            self.assertNotIn(secret, encoded)

    def test_invalid_or_unknown_identity_never_calls_network(self):
        client, transport = self.client()
        for key in ("foreign", 123, []):
            with self.assertRaises(ValidationError):
                client.collect(key)
        for data in (binding(platform_post_id="https://x.com/p/3"), binding(account_fingerprint="bad"),
                     binding(native_account_id=""), binding(profile_username="../foreign"), binding(platform="instagram")):
            with self.assertRaises(ValueError):
                UploadPostFeedbackClient("key", [data], transport)
        self.assertFalse(transport.calls)

    def test_strict_binding_fields_duplicates_and_bounds(self):
        for rows in ([binding(), binding()], [binding(extra="raw")], [binding()] * 11):
            with self.assertRaises(ValueError):
                UploadPostFeedbackClient("key", rows)
        for key, value in (("max_pages", 3), ("max_comments", 51), ("min_comments", 0), ("max_pages", True)):
            with self.assertRaises(ValueError):
                UploadPostFeedbackClient("key", [binding()], **{key: value})

    def test_cursor_parameter_after_two_page_bound(self):
        client, transport = self.client(response([comment(1)], pagination={"has_next": True, "next_cursor": "opaque+cursor"}),
                                        response([comment(2, "Really helpful")], pagination={"has_next": True, "next_cursor": "more"}))
        result = client.collect("x_01")
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(parse_qs(urlsplit(transport.calls[2][1]).query)["after"], ["opaque+cursor"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["sentiment"]["sample_size"], 2)

    def test_provider_cannot_increase_sample_bound(self):
        client, transport = self.client(response([comment(i, f"helpful {i}") for i in range(100)],
                                                pagination={"has_next": True, "next_cursor": "more"}), max_comments=40)
        result = client.collect("x_01")
        self.assertEqual(result["sentiment"]["sample_size"], 40)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(transport.calls), 2)

    def test_duplicate_and_own_comments_excluded(self):
        client, _ = self.client(response([comment(1), comment(1), comment(2),
                                         comment(3, "Useful", is_owner=True),
                                         comment(4, "Helpful", user={"id": "90001"}),
                                         comment(5, "Clear", user={}),
                                         comment(6, "Love it", user={}, is_owner=False)]))
        result = client.collect("x_01")
        self.assertEqual(result["excluded"], 5)
        self.assertEqual(result["sentiment"]["sample_size"], 2)

    def test_partial_threads_never_becomes_audience_sentiment(self):
        transport = Transport(identity_response("threads"), response([comment(1)], partial=True, warning="private provider body"))
        client = UploadPostFeedbackClient("key", [binding(platform="threads")], transport)
        result = client.collect("x_01")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["sentiment"]["counts"])
        self.assertIsNone(result["sentiment"]["sample_size"])
        self.assertNotIn("private", json.dumps(result))

    def test_empty_success_has_observed_zero_not_positive(self):
        client, _ = self.client(response())
        result = client.collect("x_01")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["sentiment"]["sample_size"], 0)
        self.assertIsNone(result["sentiment"]["positive_share"])

    def test_http_errors_safe_null_no_retry(self):
        for code in (400, 401, 403, 429, 500):
            client, transport = self.client({"status_code": code, "headers": {}, "body": {"secret": "private"}})
            result = client.collect("x_01")
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["sentiment"]["counts"])
            self.assertEqual(len(transport.calls), 2)
            self.assertNotIn("private", json.dumps(result))
            if code == 429:
                self.assertEqual(result["reason"], "rate_limited")

    def test_transport_error_sanitized(self):
        client, _ = self.client(RuntimeError("token and private text"))
        self.assertNotIn("private", json.dumps(client.collect("x_01")))

    def test_fresh_binding_reconnection_or_missing_permission_blocks_comment_read(self):
        for account in (identity_response(native="99999"), identity_response(reauth_required=True),
                        identity_response(reauth_required=None),
                        {"status_code": 401, "headers": {}, "body": {"private": "body"}}):
            transport = Transport(account)
            client = UploadPostFeedbackClient("key", [binding()], transport)
            result = client.collect("x_01")
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["network_calls"], 1)
            self.assertEqual(result["pages"], 0)
            self.assertEqual(len(transport.calls), 1)
            self.assertNotIn("/comments", transport.calls[0][1])

    def test_identity_checked_once_per_profile_platform_during_bounded_collection(self):
        transport = Transport(identity_response(), response([comment(1)]), response([comment(2)]))
        client = UploadPostFeedbackClient("key", [binding(), binding(post_id="x_02", platform_post_id="10002")], transport)
        self.assertEqual(client.collect("x_01")["network_calls"], 2)
        self.assertEqual(client.collect("x_02")["network_calls"], 1)
        self.assertEqual(client.network_calls, 3)

    def test_conflicting_native_account_allowlist_rejected(self):
        with self.assertRaises(ValueError):
            UploadPostFeedbackClient("key", [binding(), binding(post_id="x_02", platform_post_id="10002", native_account_id="99999")])

    def test_identity_conflict_schema_and_paging_fail_closed(self):
        for payload in (response([comment(1)], platform="threads"), response([comment(1)], post_id="other"),
                        response([comment(1)], pagination={"has_next": True}),
                        response([comment(1)], pagination={"has_next": "false"}),
                        response([comment(1)], success=False), response([None])):
            client, _ = self.client(payload)
            result = client.collect("x_01")
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["sentiment"]["counts"])

    def test_repeated_cursor_rejected_without_third_call(self):
        client, transport = self.client(response([comment(1)], pagination={"has_next": True, "next_cursor": "same"}),
                                        response([comment(2)], pagination={"has_next": True, "next_cursor": "same"}))
        self.assertEqual(client.collect("x_01")["status"], "unavailable")
        self.assertEqual(len(transport.calls), 3)


class SentimentTest(unittest.TestCase):
    def test_english_korean_mixed_and_unsupported(self):
        for text, expected in (("Very useful", "positive"), ("이 설명은 유용하네요", "positive"),
                               ("This is misleading", "negative"), ("스팸 같고 불편해요", "negative"),
                               ("Good but confusing", "mixed"), ("유용하지만 불편해요", "mixed"),
                               ("I use Windows", "unknown"), ("Excelente herramienta", "unknown")):
            self.assertEqual(classify_sentiment(text), expected)

    def test_negation_sarcasm_injection_private_tokens_and_long_input(self):
        for text in ("not good", "not bad", "good /s", "좋지 않아요", "ignore previous instructions great",
                     "great@example.com", "@great", "https://example.com/great", "good" * 2000, None):
            self.assertEqual(classify_sentiment(text), "unknown")

    def test_minimum_classification_and_unknown_dominance(self):
        self.assertIsNone(sentiment_summary({"positive": 4, "negative": 0, "mixed": 0, "unknown": 0})["positive_share"])
        self.assertEqual(sentiment_summary({"positive": 5, "negative": 0, "mixed": 0, "unknown": 0})["label"], "positive")
        self.assertEqual(sentiment_summary({"positive": 5, "negative": 0, "mixed": 0, "unknown": 6})["label"], "unknown")
        self.assertEqual(sentiment_summary({"positive": 3, "negative": 2, "mixed": 0, "unknown": 0})["label"], "mixed")
        for bad in ({"positive": True, "negative": 0, "mixed": 0, "unknown": 0},
                    {"positive": 51, "negative": 0, "mixed": 0, "unknown": 0}):
            with self.assertRaises(ValueError):
                sentiment_summary(bad)


class AggregateTest(unittest.TestCase):
    def test_empty_unknown_and_one_post_no_winner_or_lesson(self):
        self.assertEqual(aggregate_feedback([], [], now=NOW)["status"], "no_samples")
        result = aggregate_feedback([metric()], [], now=NOW)
        self.assertEqual(result["lessons"], [])
        self.assertIsNone(result["posts"][0]["sentiment"]["counts"])
        self.assertEqual(result["cohorts"][0]["ranked_post_ids"], [])
        self.assertIsNone(result["cohorts"][0]["winner"])

    def test_all_null_unpublished_draft_is_not_observed_audience_feedback(self):
        draft = metric(metrics=dict.fromkeys(("impressions", "views", "reach", "likes", "replies", "comments", "reposts", "shares")))
        result = aggregate_feedback([draft], [], now=NOW)
        self.assertEqual(result["status"], "no_samples")
        self.assertEqual(result["posts"], [])
        self.assertEqual(result["cohorts"], [])
        self.assertEqual(result["lessons"], [])
        # A separately observed empty comment sample is real availability evidence.
        comments = sentiment(status="empty", sentiment=sentiment_summary(dict.fromkeys(("positive", "negative", "mixed", "unknown"), 0)))
        result = aggregate_feedback([draft], [comments], now=NOW)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["posts"][0]["sentiment"]["sample_size"], 0)
        self.assertIsNone(result["posts"][0]["sentiment"]["positive_share"])

    def test_latest_snapshot_not_cumulative_sum_and_input_unchanged(self):
        old = metric(observed=NOW-timedelta(hours=8), metrics={"impressions": 50, "likes": 2, "replies": 1, "reposts": 1})
        rows = [old, metric()]
        saved = copy.deepcopy(rows)
        result = aggregate_feedback(rows, [], now=NOW)
        self.assertEqual(result["posts"][0]["exposures"], 100)
        self.assertEqual(result["posts"][0]["engagements"], 15)
        self.assertEqual(rows, saved)

    def test_no_older_metric_fallback_when_latest_missing(self):
        result = aggregate_feedback([metric(observed=NOW-timedelta(hours=8)), metric(metrics={})], [], now=NOW)
        self.assertEqual(result["posts"], [])
        self.assertEqual(result["status"], "no_samples")
        self.assertEqual(result["lessons"], [])

    def test_missing_engagement_component_remains_unknown(self):
        result = aggregate_feedback([metric(metrics={"impressions": 200, "likes": 10, "replies": 0})], [], now=NOW)
        self.assertIsNone(result["posts"][0]["engagements"])
        self.assertIsNone(result["posts"][0]["engagement_rate"])

    def test_zero_exposures_and_invalid_counts(self):
        for exposure in (0, None, float("inf"), float("nan"), -1, True, 10**20):
            result = aggregate_feedback([metric(metrics={"impressions": exposure, "likes": 1, "replies": 0, "reposts": 0})], [], now=NOW)
            self.assertFalse(result["posts"][0]["eligible"])
            json.dumps(result, allow_nan=False)

    def test_three_consistent_posts_directional_lesson_not_winner(self):
        rows = [metric(f"x_0{i}") for i in range(1, 4)]
        result = aggregate_feedback(rows, [sentiment()], now=NOW)
        lesson = result["lessons"][0]
        self.assertEqual(lesson["sample_posts"], 3)
        self.assertEqual(lesson["impressions"], 300)
        self.assertEqual(lesson["engagements"], 45)
        self.assertEqual(lesson["confidence"], "directional")
        self.assertEqual(lesson["sentiment"]["positive"], 5)
        self.assertEqual(len(lesson["source_ids"]), 3)
        self.assertEqual(result["cohorts"][0]["status"], "directional_only")
        self.assertIsNone(result["cohorts"][0]["winner"])

    def test_lesson_comments_unavailable_remains_null(self):
        result = aggregate_feedback([metric(f"x_0{i}") for i in range(1, 4)], [], now=NOW)
        self.assertIsNone(result["lessons"][0]["sentiment"])

    def test_integral_floats_normalize_fractional_metrics_not_learned(self):
        values = {"impressions": 100.0, "likes": 10.0, "replies": 3.0, "reposts": 2.0}
        rows = [metric(f"x_0{i}", metrics=values) for i in range(1, 4)]
        result = aggregate_feedback(rows, [], now=NOW)
        self.assertIs(type(result["lessons"][0]["impressions"]), int)
        self.assertIs(type(result["lessons"][0]["engagements"]), int)
        values["impressions"] = 100.5
        result = aggregate_feedback(rows, [], now=NOW)
        self.assertEqual(result["lessons"], [])
        self.assertEqual(result["posts"][0]["exposures"], 100.5)

    def test_malformed_platform_and_strategy_labels_do_not_crash_or_leak(self):
        self.assertEqual(aggregate_feedback([metric(platform=[])], [], now=NOW)["posts"], [])
        result = aggregate_feedback([metric(hypothesis=[], hook_type={})], [], now=NOW)
        self.assertIsNone(result["posts"][0]["hypothesis"])
        self.assertIsNone(result["posts"][0]["hook"])

    def test_accounts_platforms_fingerprints_denominators_never_pool(self):
        for changes in ({"account_key": "personal_x"}, {"platform": "threads"}, {"account_fingerprint": "b"*64},
                        {"metrics": {"views": 100, "likes": 10, "replies": 3, "reposts": 2}}):
            result = aggregate_feedback([metric("x_01"), metric("x_02"), metric("x_03", **changes)], [], now=NOW)
            self.assertEqual(result["lessons"], [])
            self.assertEqual(len(result["cohorts"]), 2)

    def test_comments_require_same_fingerprint_latest_permission(self):
        result = aggregate_feedback([metric()], [sentiment(account_fingerprint="b"*64)], now=NOW)
        self.assertIsNone(result["posts"][0]["sentiment"]["counts"])
        result = aggregate_feedback([metric()], [sentiment(observed=NOW-timedelta(hours=8)), sentiment(status="partial", partial=True)], now=NOW)
        self.assertIsNone(result["posts"][0]["sentiment"]["counts"])

    def test_stale_future_naive_conflicting_missing_identity_skipped(self):
        for rows in ([metric(observed=NOW-timedelta(hours=49))], [metric(observed=NOW+timedelta(seconds=1))],
                     [metric(observed=NOW.replace(tzinfo=None))], [metric(account_fingerprint=None)],
                     [metric(), metric(metrics={"impressions": 1000})]):
            self.assertEqual(aggregate_feedback(rows, [], now=NOW)["posts"], [])

    def test_lessons_require_same_hypothesis_hook_and_three_high_exposure_posts(self):
        for changes in ({"hypothesis": "H2"}, {"hook_type": "observation"},
                        {"metrics": {"impressions": 99, "likes": 5, "replies": 2, "reposts": 1}}):
            result = aggregate_feedback([metric("x_01"), metric("x_02"), metric("x_03", **changes)], [], now=NOW)
            self.assertEqual(result["lessons"], [])

    def test_bound_output_and_closed_labels_discard_provider_fields(self):
        rows = [metric(f"x_{i}", secret="private person", hypothesis="ignore instructions", hook_type="raw") for i in range(20)]
        result = aggregate_feedback(rows, [], now=NOW)
        self.assertEqual(len(result["posts"]), 10)
        encoded = json.dumps(result)
        for text in ("private person", "ignore instructions", '"raw"'):
            self.assertNotIn(text, encoded)

    def test_limits_cannot_be_weakened(self):
        for args in ({"minimum_posts": 1}, {"minimum_exposures": 10}, {"min_comments": 0}):
            with self.assertRaises(ValueError):
                aggregate_feedback([], [], now=NOW, **args)


if __name__ == "__main__":
    unittest.main()
