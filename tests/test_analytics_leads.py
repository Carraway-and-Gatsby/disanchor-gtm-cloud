import copy
import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from gtm.analytics import EVENTS, GA4Client, MockGA4, ReportUnavailable
from gtm.leads import LeadApp, LeadStore, aggregate_leads


def report(dimensions, metrics, rows, **extras):
    return {"dimensionHeaders": [{"name": name} for name in dimensions],
            "metricHeaders": [{"name": name} for name in metrics],
            "rows": [{"dimensionValues": [{"value": str(v)} for v in dims], "metricValues": [{"value": str(v)} for v in values]} for dims, values in rows],
            "rowCount": len(rows), **extras}


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def transport(self, url, body, headers):
        self.calls.append((url, body, headers))
        dimensions = [item["name"] for item in body["dimensions"]]
        metrics = [item["name"] for item in body["metrics"]]
        if dimensions == []:
            return report(dimensions, metrics, [([], [8, 12, 7])])
        if dimensions == ["eventName"]:
            return report(dimensions, metrics, [(["page_view"], [14]), (["generate_lead"], [2])])
        if dimensions == ["sessionSource", "sessionMedium"]:
            return report(dimensions, metrics, [(["x", "organic_social"], [6, 9, 5]), (["threads", "organic_social"], [4, 3, 2])])
        if dimensions == ["sessionSource", "sessionMedium", "eventName"]:
            return report(dimensions, metrics, [(["x", "organic_social", "page_view"], [10]), (["x", "organic_social", "generate_lead"], [2]), (["threads", "organic_social", "page_view"], [4])])
        if dimensions == ["sessionManualAdContent"]:
            return report(dimensions, metrics, [(["c_research_001"], [6, 9, 5]), (["person@example.com"], [1, 1, 1])])
        return report(dimensions, metrics, [(["c_research_001", "page_view"], [10])])

    def test_real_values_and_non_additive_users(self):
        result = GA4Client("123", "token", self.transport, include_content=True).collect("2026-10-01", "2026-10-24", "research-2026")
        self.assertEqual(result["totals"]["users"], 8)
        self.assertEqual(sum(row["users"] for row in result["channels"]), 10)
        self.assertEqual([row["source"] for row in result["channels"]], ["x", "threads"])
        self.assertTrue(all(row["medium"] == "organic_social" for row in result["channels"]))
        self.assertEqual(result["totals"]["event_counts"]["generate_lead"], 2)
        self.assertEqual(result["totals"]["event_counts"]["cta_click"], 0)
        self.assertEqual(result["content"][0]["content_id"], "c_research_001")
        self.assertNotIn("person@example.com", json.dumps(result))
        self.assertEqual(len(self.calls), 6)
        for url, body, headers in self.calls:
            self.assertEqual(headers["Authorization"], "Bearer token")
            self.assertTrue(url.endswith("properties/123:runReport"))
            self.assertEqual(body["dimensionFilter"]["filter"]["fieldName"], "sessionCampaignName")
            self.assertNotIn("userId", json.dumps(body))

    def test_not_configured_does_not_request(self):
        result = GA4Client("123", "", self.transport).collect("2026-10-01", "2026-10-24", "research")
        self.assertEqual(result["missing"], {"all": "not_configured"})
        self.assertIsNone(result["totals"]["sessions"])
        self.assertFalse(self.calls)

    def test_transport_error_never_becomes_zero_or_leaks(self):
        def fail(*args):
            raise RuntimeError("credential=super-secret; email=person@example.com")
        result = GA4Client("123", "secret", fail).collect("2026-10-01", "2026-10-24", "research")
        self.assertIsNone(result["totals"]["users"])
        self.assertIsNone(result["channels"])
        self.assertEqual(result["missing"]["totals"], "report_failure")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("@", json.dumps(result))

    def test_empty_response_remains_unknown(self):
        def empty(url, body, headers):
            return report([d["name"] for d in body["dimensions"]], [m["name"] for m in body["metrics"]], [])
        result = GA4Client("123", "token", empty).collect("2026-10-01", "2026-10-24", "research")
        self.assertIsNone(result["totals"]["event_counts"]["generate_lead"])
        self.assertEqual(result["missing"]["events"], "no_rows_returned")

    def test_missing_and_restricted_metrics_are_null(self):
        def restricted(url, body, headers):
            data = self.transport(url, body, headers)
            if not body["dimensions"]:
                data["metadata"] = {"schemaRestrictionResponse": {"activeMetricRestrictions": [{"metricName": "sessions"}]}}
            return data
        result = GA4Client("123", "token", restricted).collect("2026-10-01", "2026-10-24", "research")
        self.assertEqual(result["totals"]["users"], 8)
        self.assertIsNone(result["totals"]["sessions"])
        self.assertEqual(result["missing"]["totals.sessions"], "missing_or_restricted_metric")

    def test_thresholding_does_not_invent_absent_event_zero(self):
        def limited(url, body, headers):
            data = self.transport(url, body, headers)
            data["metadata"] = {"subjectToThresholding": True}
            return data
        result = GA4Client("123", "token", limited).collect("2026-10-01", "2026-10-24", "research")
        self.assertIsNone(result["totals"]["event_counts"]["lead_form_start"])
        self.assertEqual(result["totals"]["event_counts"]["generate_lead"], 2)
        self.assertIn("events:subjectToThresholding", result["warnings"])

    def test_pagination_and_bound(self):
        def paged(url, body, headers):
            offset = int(body["offset"])
            return report([d["name"] for d in body["dimensions"]], ["eventCount"], [(["page_view" if offset == 0 else "generate_lead"], [1])], rowCount=2)
        client = GA4Client("123", "token", paged)
        rows, warnings = client._report({"start_date": "2026-10-01", "end_date": "2026-10-24"}, "research", ["eventName"], ["eventCount"])
        self.assertEqual(len(rows), 2)
        def oversized(url, body, headers):
            return report(["eventName"], ["eventCount"], [(["page_view"], [1])], rowCount=1000000)
        client.transport = oversized
        with self.assertRaisesRegex(ReportUnavailable, "row_limit_exceeded"):
            client._report({"start_date": "2026-10-01", "end_date": "2026-10-24"}, "research", ["eventName"], ["eventCount"])

    def test_validation_and_mock(self):
        with self.assertRaises(ValueError):
            GA4Client("../42", "token")
        with self.assertRaises(ValueError):
            GA4Client("123", "token").collect("2026-10-24", "2026-10-01", "research")
        with self.assertRaises(ValueError):
            GA4Client("123", "token").collect("2026-10-01", "2026-10-24", "person@example.com")
        result = MockGA4().collect("2026-10-01", "2026-10-24", "research")
        self.assertEqual(result["mode"], "mock")
        self.assertIn("synthetic_test_data", result["warnings"])

    def test_key_event_rate_is_opt_in_native_fraction(self):
        def transport(url, body, headers):
            if body["metrics"] == [{"name": "sessionKeyEventRate:generate_lead"}]:
                return report([], ["sessionKeyEventRate:generate_lead"], [([], ["0.125"])])
            return self.transport(url, body, headers)
        result = GA4Client("123", "token", transport, key_event_registered=True).collect("2026-10-01", "2026-10-24", "research")
        self.assertEqual(result["key_event_session_rate"], 0.125)
        self.assertIsNone(result["key_event_sessions"])
        unconfigured = GA4Client("123", "token", transport).collect("2026-10-01", "2026-10-24", "research")
        self.assertIsNone(unconfigured["key_event_session_rate"])
        self.assertEqual(unconfigured["missing"]["key_event_session_rate"], "generate_lead_key_event_registration_not_verified")

    def test_canonical_content_ids_are_retained(self):
        def transport(url, body, headers):
            dimensions = [d["name"] for d in body["dimensions"]]
            if dimensions == ["sessionManualAdContent"]:
                return report(dimensions, [m["name"] for m in body["metrics"]], [(["x_profile"], [4, 6, 3]), (["threads_03"], [3, 4, 2]), (["person@example.com"], [1, 1, 1])])
            return self.transport(url, body, headers)
        result = GA4Client("123", "token", transport, include_content=True).collect("2026-10-01", "2026-10-24", "research")
        self.assertEqual([r["content_id"] for r in result["content"]], ["x_profile", "threads_03"])

    def test_service_account_refresh_uses_readonly_scope_and_no_network_at_construction(self):
        calls = []
        class Credentials:
            token = None
            @classmethod
            def from_service_account_file(cls, filename, scopes):
                calls.append((filename, scopes))
                return cls()
            def before_request(self, request, method, url, headers):
                calls.append(("before", method, url))
                self.token = "refreshed-token"
        service_module = types.ModuleType("google.oauth2.service_account")
        service_module.Credentials = Credentials
        request_module = types.ModuleType("google.auth.transport.requests")
        request_module.Request = lambda: lambda *args, **kwargs: None
        with patch.dict("sys.modules", {"google.oauth2.service_account": service_module, "google.auth.transport.requests": request_module}), patch.dict("os.environ", {"GOOGLE_APPLICATION_CREDENTIALS": "/not-read/fake.json"}):
            client = GA4Client.from_service_account("123", transport=self.transport)
            self.assertEqual(calls, [("/not-read/fake.json", ["https://www.googleapis.com/auth/analytics.readonly"])])
            result = client.collect("2026-10-01", "2026-10-24", "research")
            self.assertEqual(result["totals"]["users"], 8)
            self.assertEqual(self.calls[0][2]["Authorization"], "Bearer refreshed-token")
            self.assertNotIn("refreshed-token", json.dumps(result))

    def test_failed_refresh_runs_once_per_collection_and_redacts_error(self):
        called = []
        def refresh():
            called.append(True)
            raise ValueError("private-key-secret")
        result = GA4Client("123", "", self.transport, token_provider=refresh).collect("2026-10-01", "2026-10-24", "research")
        self.assertEqual(len(called), 1)
        self.assertFalse(self.calls)
        self.assertEqual(result["missing"]["totals"], "credentials_refresh_failed")
        self.assertNotIn("private-key-secret", json.dumps(result))

    def test_rate_limit_stops_remaining_calls_in_collection(self):
        called = []
        def limited(*args):
            called.append(True)
            raise ReportUnavailable("http_429")
        result = GA4Client("123", "token", limited, include_content=True, key_event_registered=True).collect("2026-10-01", "2026-10-24", "research")
        self.assertEqual(len(called), 1)
        self.assertIsNone(result["channels"])
        self.assertEqual(result["missing"]["content"], "http_429")


class LeadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = dt.datetime(2026, 10, 24, 3, tzinfo=dt.timezone.utc).timestamp()
        self.store = LeadStore(Path(self.temp.name) / "leads.sqlite3", secret=b"x" * 32, clock=lambda: self.now)
        self.app = LeadApp(self.store, origin="https://disanchor.com", privacy_contact="privacy@example.com", consent_version="research-v1", content_registry={"c_research_001": {"campaign": "research", "channel": "reddit"}})
        self.data = {"email": "Test@Example.com", "interest": "research", "consent": True, "consent_version": "research-v1", "analytics_consent": True, "content_id": "c_research_001", "website": ""}

    def tearDown(self):
        self.temp.cleanup()

    def request(self, payload=None, *, overrides=None):
        raw = json.dumps(self.data if payload is None else payload).encode()
        environ = {"PATH_INFO": "/api/research-interest", "REQUEST_METHOD": "POST", "HTTP_ORIGIN": "https://disanchor.com", "HTTP_HOST": "disanchor.com", "wsgi.url_scheme": "https", "REMOTE_ADDR": "192.0.2.1", "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
        environ.update(overrides or {})
        result = {}
        def start(status, headers):
            result.update(status=int(status.split()[0]), headers=dict(headers))
        raw_result = b"".join(self.app(environ, start))
        result["body"] = json.loads(raw_result) if "application/json" in result["headers"]["Content-Type"] else raw_result.decode()
        return result

    def test_new_only_once_and_aggregate_no_pii(self):
        first, second = self.request(), self.request()
        self.assertEqual(first["body"], {"ok": True, "new": True})
        self.assertEqual(second["body"], {"ok": True, "new": False})
        aggregate = self.store.aggregate("2026-10-24", "2026-10-24", "research")
        self.assertEqual(aggregate["stored_leads"], 1)
        self.assertIsNone(aggregate["qualified_leads"])
        self.assertEqual(aggregate["by_channel"], [{"channel": "reddit", "stored_leads": 1}])
        self.assertNotIn("@", json.dumps(aggregate))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_mandatory_research_consent_and_current_notice(self):
        for patch in ({"consent": False}, {"consent": "true"}, {"consent_version": "old"}, {"email": "bad"}, {"interest": "medical-history"}):
            self.assertEqual(self.request({**self.data, **patch})["status"], 400)
        self.assertEqual(self.store.aggregate("2026-10-24", "2026-10-24")["stored_leads"], 0)

    def test_no_analytics_consent_means_no_attribution(self):
        self.assertTrue(self.request({**self.data, "analytics_consent": False})["body"]["new"])
        self.assertEqual(self.store.aggregate("2026-10-24", "2026-10-24", "research")["stored_leads"], 0)
        self.assertEqual(self.store.aggregate("2026-10-24", "2026-10-24")["by_channel"], [{"channel": None, "stored_leads": 1}])

    def test_unregistered_content_not_stored(self):
        self.request({**self.data, "content_id": "person@example.com"})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT campaign, channel, content_id FROM leads").fetchone(), (None, None, None))

    def test_origin_host_https_mandatory(self):
        for overrides in ({"HTTP_ORIGIN": "https://evil.example"}, {"HTTP_ORIGIN": ""}, {"HTTP_HOST": "evil.example"}, {"wsgi.url_scheme": "http"}):
            self.assertEqual(self.request(overrides=overrides)["status"], 403)

    def test_post_json_and_size_limits(self):
        self.assertEqual(self.request(overrides={"REQUEST_METHOD": "GET"})["status"], 405)
        self.assertEqual(self.request(overrides={"CONTENT_TYPE": "text/plain"})["status"], 415)
        self.assertEqual(self.request(overrides={"CONTENT_LENGTH": "4097"})["status"], 413)
        self.assertEqual(self.request(overrides={"CONTENT_LENGTH": "bad"})["status"], 400)
        self.assertEqual(self.request(overrides={"wsgi.input": io.BytesIO(b"{")})["status"], 400)

    def test_honeypot_silent_no_storage(self):
        self.assertEqual(self.request({**self.data, "website": "https://spam.invalid"})["body"], {"ok": True, "new": False})
        self.assertEqual(self.store.aggregate("2026-10-24", "2026-10-24")["stored_leads"], 0)

    def test_shared_rate_limit_ignores_forwarded_headers(self):
        for _ in range(5):
            self.assertEqual(self.request()["status"], 200)
        self.assertEqual(self.request(overrides={"HTTP_X_FORWARDED_FOR": "198.51.100.99"})["status"], 429)
        other = LeadStore(self.store.path, secret=b"x" * 32, clock=lambda: self.now)
        self.assertFalse(other.allow_attempt("192.0.2.1"))
        with self.store.connect() as db:
            stored = repr(db.execute("SELECT * FROM rate_limits").fetchall())
        self.assertNotIn("192.0.2.1", stored)
        self.now += 601
        self.assertEqual(self.request()["status"], 200)

    def test_no_public_leads_or_metrics_routes(self):
        for path in ("/api/leads", "/api/leads/aggregate", "/leads.sqlite3"):
            self.assertEqual(self.request(overrides={"PATH_INFO": path, "REQUEST_METHOD": "GET"})["status"], 404)

    def test_retention_and_deletion(self):
        self.request()
        self.now += 31 * 86400
        self.assertEqual(self.store.aggregate("2026-10-24", "2026-10-24")["stored_leads"], 0)
        self.assertEqual(self.store.prune(), 1)
        self.request()
        self.assertEqual(self.store.delete_by_email("test@example.com"), 1)
        self.assertEqual(self.store.delete_by_email("test@example.com"), 0)

    def test_form_notice_and_security_headers(self):
        response = self.request(overrides={"PATH_INFO": "/research-interest", "REQUEST_METHOD": "GET"})
        self.assertEqual(response["status"], 200)
        self.assertIn("research-v1", response["body"])
        self.assertNotIn("{{", response["body"])
        self.assertIn("privacy@example.com", response["body"])
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response["headers"]["Content-Security-Policy"])

    def test_secure_constructor_and_local_boundary(self):
        with self.assertRaises(ValueError):
            LeadStore(Path(self.temp.name) / "bad.db", secret=b"short")
        with self.assertRaises(ValueError):
            LeadApp(self.store, origin="http://disanchor.com", privacy_contact="privacy@example.com", consent_version="v1")
        self.app = LeadApp(self.store, origin="http://127.0.0.1:8081", privacy_contact="privacy@example.com", consent_version="research-v1", allow_local_http=True)
        overrides = {"wsgi.url_scheme": "http", "HTTP_HOST": "127.0.0.1:8081", "HTTP_ORIGIN": "http://127.0.0.1:8081"}
        self.assertEqual(self.request(overrides=overrides)["status"], 403)
        self.assertEqual(self.request(overrides={**overrides, "REMOTE_ADDR": "127.0.0.1"})["status"], 200)

    def test_readonly_exact_window_excludes_boundary_and_needs_no_secret(self):
        start = dt.datetime.fromtimestamp(self.now, dt.timezone.utc)
        self.now -= 1
        self.request({**self.data, "email": "before-start@example.com"})
        self.now += 1
        self.request()
        self.now += 3600
        self.request({**self.data, "email": "second@example.com"})
        end = dt.datetime.fromtimestamp(self.now, dt.timezone.utc)
        before = self.store.path.stat()
        result = aggregate_leads(self.store.path, start, end, "research", as_of=end)
        self.assertEqual(result["stored_leads"], 1)
        self.assertEqual(result["period"]["start_at"], start.isoformat())
        self.assertTrue(result["period"]["end_exclusive"])
        self.assertEqual(self.store.path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertNotIn("@", json.dumps(result))
        missing = Path(self.temp.name) / "missing.sqlite3"
        with self.assertRaises(ValueError):
            aggregate_leads(missing, start, end)
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            aggregate_leads(self.store.path, "2026-10-24", end)

    def test_production_content_registry_accepts_experiment_utm_ids(self):
        self.app = LeadApp(self.store, origin="https://disanchor.com", privacy_contact="privacy@example.com", consent_version="research-v1", content_registry={"threads_03": {"campaign": "disanchor_gtm_7d", "channel": "threads"}})
        self.request({**self.data, "content_id": "threads_03"})
        self.assertEqual(self.store.aggregate("2026-10-24", "2026-10-24", "disanchor_gtm_7d")["by_channel"], [{"channel": "threads", "stored_leads": 1}])


class TrackingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed for the JavaScript contract test")
    def test_consent_new_success_duplicate_failure_and_redaction(self):
        # Exercise the shipped script itself with a small DOM/transport harness.
        source = Path(__file__).resolve().parents[1] / "web" / "tracking.js"
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
function setup({consent=true, result={ok:true,new:true}, ok=true, fail=false}={}) {
  const handlers = {}, emitted = [], requests = [], button = {}, status = {};
  const fields = {email:'private@example.com',interest:'research',consent:'on',website:''};
  const form = {dataset:{noticeVersion:'research-v1',contentIds:'["c_research_001"]'},
    addEventListener:(n,fn)=>handlers[n]=fn,reportValidity:()=>true,
    querySelector:()=>button,reset:()=>{}};
  const cta = {addEventListener:(n,fn)=>handlers.cta=fn};
  const context = {URLSearchParams, FormData: class {get(n){return fields[n];}},
    document:{getElementById:n=>n==='research-form'?form:status,querySelectorAll:()=>[cta]},
    window:{location:{origin:'https://disanchor.com',search:'?utm_content=c_research_001&email=private@example.com'}},
    fetch:async(url,options)=>{requests.push({url,options});if(fail)throw Error('failure');return {ok,status:ok?200:400,json:async()=>result};}};
  vm.runInNewContext(source, context);
  const api=context.window.DisanchorTracking;
  const submit=()=>handlers.submit({preventDefault(){}});
  return {handlers,emitted,requests,status,api,submit, configure:()=>api.configure({hasAnalyticsConsent:()=>consent,emit:(name,params)=>emitted.push({name,params})})};
}
(async()=>{
  let s=setup(); await s.submit(); assert.equal(s.emitted.length,0); // default disabled
  s=setup(); s.configure(); s.handlers.focusin();s.handlers.focusin();s.handlers.cta();
  assert.equal(s.api.event('generate_lead'),false); await s.submit();
  assert.deepEqual(s.emitted.map(e=>e.name),['view_research_form','lead_form_start','cta_click','generate_lead']);
  assert(!JSON.stringify(s.emitted).includes('private@example.com'));
  assert(!JSON.stringify(s.emitted).includes('?'));
  assert.equal(s.requests[0].url,'/api/research-interest');
  assert.equal(s.requests[0].options.redirect,'error');
  s=setup({result:{ok:true,new:false}});s.configure();await s.submit();
  assert(!s.emitted.some(e=>e.name==='generate_lead'));
  for(const options of [{consent:false},{ok:false},{fail:true},{result:{ok:true,new:'true'}}]){
    s=setup(options);s.configure();await s.submit();
    assert(!s.emitted.some(e=>e.name==='generate_lead'));
    if(options.consent===false){assert.equal(s.emitted.length,0);assert.equal(JSON.parse(s.requests[0].options.body).content_id,null);}
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-e", script, str(source)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
