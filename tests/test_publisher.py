import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from gtm.publisher import (
    AmbiguousCancellation, AmbiguousSubmission, AuthenticationError,
    MockPublisher, PlanRestrictionError, ProtocolError,
    ProviderUnavailableError, RateLimitError, UploadPostPublisher, ValidationError,
)


class RecordingTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def response(body, code=200, headers=None):
    return {"status_code": code, "headers": headers or {}, "body": body}


class PublisherTest(unittest.TestCase):
    def setUp(self):
        self.when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    def publisher(self, *responses):
        transport = RecordingTransport(*responses)
        return UploadPostPublisher("secret-do-not-log", "brand", transport), transport

    def test_schedule_uses_documented_fields_and_real_job_only(self):
        publisher, transport = self.publisher(response({"success": True, "job_id": "job_42"}, 202))
        result = publisher.schedule("threads", "A factual post", self.when, "key_42")
        method, url, headers, body = transport.calls[0]
        self.assertEqual((method, url), ("POST", "https://api.upload-post.com/api/upload_text"))
        self.assertEqual(headers["Authorization"], "Apikey secret-do-not-log")
        self.assertEqual(headers["Idempotency-Key"], "key_42")
        fields = parse_qs(body.decode())
        self.assertEqual(fields["platform[]"], ["threads"])
        self.assertEqual(fields["external_id"], ["key_42"])
        self.assertEqual(fields["request_id"], ["key_42"])
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["provider_job_id"], "job_42")
        self.assertIsNone(result["published_url"])
        self.assertIsNone(result["metrics"]["impressions"])
        self.assertFalse(result["simulated"])

    def test_timeout_is_ambiguous_sanitized_and_never_retried(self):
        publisher, transport = self.publisher(TimeoutError("secret-do-not-log body private text"))
        with self.assertRaises(AmbiguousSubmission) as caught:
            publisher.schedule("threads", "A post", self.when, "key")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_http_failures_classified_without_error_body(self):
        for code, cls in ((401, AuthenticationError), (403, PlanRestrictionError),
                          (429, RateLimitError), (500, AmbiguousSubmission), (408, AmbiguousSubmission)):
            with self.subTest(code=code):
                publisher, transport = self.publisher(response({"error": "secret-do-not-log"}, code,
                                                               {"Retry-After": "120"}))
                with self.assertRaises(cls) as caught:
                    publisher.schedule("threads", "Text", self.when, "key")
                self.assertEqual(caught.exception.http_status, code)
                self.assertEqual(caught.exception.retry_after, 120)
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(len(transport.calls), 1)

    def test_missing_job_id_cannot_claim_success(self):
        publisher, _ = self.publisher(response({"success": True, "request_id": "request_only"}))
        with self.assertRaises(AmbiguousSubmission):
            publisher.schedule("threads", "Text", self.when, "key")

    def test_x_link_or_long_thread_safety_rejects_before_network(self):
        publisher, transport = self.publisher()
        for text in ("Go https://example.com", "Go example.com/path", "Go bit.ly/abc",
                     "Go example[.]com", "Go hxxp://example.com", "가" * 141, "a" * 281):
            with self.subTest(text=text[:20]), self.assertRaises(ValidationError):
                publisher.schedule("x", text, self.when, "key")
        self.assertEqual(transport.calls, [])

    def test_invalid_schedule_and_unsupported_media_fail_before_network(self):
        publisher, transport = self.publisher()
        for when in ("2028-01-01", "2020-01-01T00:00:00Z", "bad"):
            with self.assertRaises(ValidationError):
                publisher.schedule("threads", "Text", when, "key")
        with self.assertRaises(ValidationError):
            publisher.schedule("instagram", "Text", self.when, "key")
        for platform in ("linkedin", "bluesky"):
            with self.assertRaises(ValidationError):
                publisher.schedule(platform, "Text", self.when, "key")
        with self.assertRaises(ValidationError):
            publisher.schedule("threads", "Text", self.when, "key\r\nleak")
        self.assertFalse(transport.calls)

    def test_status_completed_skipped_is_not_published(self):
        publisher, _ = self.publisher(response({"job_id": "job", "status": "completed", "results": [
            {"platform": "linkedin", "success": False, "skipped": True,
             "error": "private provider account details"}]}))
        result = publisher.status("job")
        self.assertEqual(result["status"], "skipped")
        self.assertNotIn("private", repr(result))

    def test_status_list_and_object_results(self):
        for results in ([{"platform": "x", "success": True, "post_id": "42", "url": "https://x.com/u/status/42"}],
                        {"x": {"success": True, "post_id": "42", "url": "https://x.com/u/status/42"}}):
            publisher, _ = self.publisher(response({"status": "completed", "results": results}))
            result = publisher.status("job")
            self.assertEqual(result["status"], "published")
            self.assertEqual(result["platform_post_id"], "42")
            self.assertEqual(result["published_url"], "https://x.com/u/status/42")
            self.assertIsInstance(json.dumps(result), str)

    def test_queued_success_is_not_published(self):
        publisher, _ = self.publisher(response({"status": "in_progress", "results": [
            {"platform": "x", "success": True, "message": "Queued"}]}))
        self.assertEqual(publisher.status("job")["status"], "pending")

    def test_history_paginates_and_excludes_foreign_profiles(self):
        row = {"profile_username": "brand", "platform": "linkedin", "success": True,
               "job_id": "scheduled_1", "request_id": "request_1", "platform_post_id": "native_1",
               "post_url": "https://www.linkedin.com/feed/update/native_1", "external_id": "key_1"}
        publisher, transport = self.publisher(response({"history": [row] * 100, "total": 101}),
                                              response({"history": [dict(row, profile_username="other")], "total": 101}))
        history = publisher.history()
        self.assertEqual(len(history), 100)
        self.assertEqual(len(transport.calls), 2)
        query = parse_qs(urlsplit(transport.calls[1][1]).query)
        self.assertEqual(query["page"], ["2"])
        self.assertEqual(query["profile_username"], ["brand"])
        self.assertEqual(history[0]["provider_request_id"], "request_1")
        self.assertEqual(history[0]["external_id"], "key_1")

    def test_post_metrics_never_use_profile_snapshots_or_fake_missing_zero(self):
        publisher, _ = self.publisher(response({"platforms": {"linkedin": {
            "success": True, "platform_post_id": "42", "post_metrics": {"likes": 0, "clicks": 7},
            "profile_snapshot_latest": {"impressions": 123456},
        }}}))
        item = publisher.analytics(["request_1"])["posts"][0]
        self.assertEqual(item["metrics"]["likes"], 0)
        self.assertEqual(item["metrics"]["clicks"], 7)
        self.assertIsNone(item["metrics"]["link_clicks"])
        self.assertEqual(item["metric_missing_reasons"]["link_clicks"], "provider_not_returned")
        self.assertIsNone(item["metrics"]["impressions"])
        self.assertEqual(item["metric_missing_reasons"]["impressions"], "provider_not_returned")
        self.assertIsNone(item["metrics"]["leads"])
        self.assertEqual(item["metrics_scope"], "post")

    def test_missing_post_metrics_with_provider_error_remains_null(self):
        publisher, _ = self.publisher(response({"platforms": {"threads": {
            "success": True, "post_metrics_error": "secret token expired"}}}))
        result = publisher.analytics(["request_1"])
        self.assertEqual(result["posts"][0]["metric_missing_reasons"]["views"], "provider_post_metrics_unavailable")
        self.assertNotIn("secret", repr(result))

    def test_quota_unknown_cannot_infer_remaining_from_plan(self):
        publisher, _ = self.publisher(response({"success": True, "plan": "Default"}))
        result = publisher.quota()
        self.assertIsNone(result["used"])
        self.assertIsNone(result["limit"])
        self.assertIsNone(result["remaining"])
        self.assertEqual(result["missing_reasons"]["remaining"], "provider_not_returned")

    def test_quota_known_zero_is_preserved(self):
        publisher, _ = self.publisher(response({"plan": "Default", "api_usage": {"count": 10, "limit": 10}}))
        self.assertEqual(publisher.quota()["remaining"], 0)

    def test_cancel_verifies_success_and_keeps_ambiguous_failure(self):
        publisher, transport = self.publisher(response({"success": True, "credits_refunded": 1}))
        self.assertEqual(publisher.cancel("job")["status"], "canceled")
        self.assertEqual(transport.calls[0][:2], ("DELETE", "https://api.upload-post.com/api/uploadposts/schedule/job"))
        publisher, _ = self.publisher(TimeoutError("secret"))
        with self.assertRaises(AmbiguousCancellation):
            publisher.cancel("job")

    def test_read_timeout_is_not_submission_timeout(self):
        publisher, _ = self.publisher(TimeoutError("secret"))
        with self.assertRaises(ProviderUnavailableError):
            publisher.quota()

    def test_malformed_json_contract_is_ambiguous_only_on_write(self):
        publisher, _ = self.publisher(response([]))
        with self.assertRaises(AmbiguousSubmission):
            publisher.schedule("threads", "Text", self.when, "key")
        publisher, _ = self.publisher(response([]))
        with self.assertRaises(ProtocolError):
            publisher.quota()

    def test_mock_is_deterministic_idempotent_and_clearly_simulated(self):
        a, b = MockPublisher(user="brand"), MockPublisher(user="brand")
        self.assertTrue(a.simulated)
        self.assertFalse(UploadPostPublisher.simulated)
        result = a.schedule("threads", "Text", self.when, "key")
        self.assertEqual(result, b.schedule("threads", "Text", self.when, "key"))
        self.assertEqual(result, a.schedule("threads", "Text", self.when, "key"))
        self.assertTrue(result["simulated"])
        self.assertIsNone(result["published_url"])
        self.assertIsNone(result["metrics"]["views"])
        with self.assertRaises(ValidationError):
            a.schedule("threads", "Different", self.when, "key")
        result["status"] = "modified_by_caller"
        self.assertEqual(a.status(result["provider_job_id"])["status"], "scheduled")
        self.assertEqual(a.cancel(result["provider_job_id"])["status"], "canceled")


if __name__ == "__main__":
    unittest.main()
