import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from gtm.accounts import AccountRegistry
from gtm.bound_publisher import AccountBoundPublisher
from gtm.publisher import AuthenticationError, ProtocolError, RateLimitError, ValidationError


KEY = "bound-fake-credential-never-output"


class Transport:
    def __init__(self):
        self.calls = []
        self.accounts = {
            "builder": {"x": {"username": "x-1", "handle": "builder", "reauth_required": False}},
            "brand": {"threads": {"username": "t-1", "handle": "disanchor", "reauth_required": False}},
        }
        self.history = {}
        self.failure = None

    def __call__(self, method, url, headers, body):
        parts=urlsplit(url)
        query=parse_qs(parts.query)
        self.calls.append((method, parts.path, query, parse_qs(body.decode()) if body else None))
        if self.failure:
            return {"status_code":self.failure,"headers":{},"body":{"message":KEY}}
        path=parts.path.removeprefix("/api")
        if method=="GET" and path.startswith("/uploadposts/users/"):
            name=unquote(path.rsplit("/",1)[-1])
            result={"success":True,"profile":{"username":name,"social_accounts":self.accounts[name]}}
        elif method=="POST" and path=="/upload_text":
            fields=parse_qs(body.decode())
            result={"success":True,"job_id":fields["user"][0]+"-"+fields["platform[]"][0]+"-job"}
        elif method=="GET" and path=="/uploadposts/me":
            result={"success":True,"plan":"default","api_usage":{"count":3,"limit":10},"email":KEY}
        elif method=="GET" and path=="/uploadposts/status":
            result={"status":"queued","job_id":query["job_id"][0],"results":[]}
        elif method=="DELETE" and path.startswith("/uploadposts/schedule/"):
            result={"success":True}
        elif method=="GET" and path.startswith("/uploadposts/post-analytics/"):
            result={"platforms":{"threads":{"post_metrics":{"views":12}}}}
        elif method=="GET" and path=="/uploadposts/history":
            rows=self.history.get(query["profile_username"][0],[])
            result={"history":rows,"total":len(rows)}
        else:
            raise AssertionError("unexpected provider call")
        return {"status_code":200,"headers":{},"body":copy.deepcopy(result)}


class BoundPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.transport=Transport()
        self.targets={"x":"founder-x","threads":"brand-threads"}
        self.when=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()

    def registry(self, *, shared=False, reviewed=True, missing_id=False):
        rows=[]
        folder=self.root / "prompts" / "accounts"
        folder.mkdir(parents=True,exist_ok=True)
        for platform,key in self.targets.items():
            profile="builder" if shared or platform=="x" else "brand"
            filename=f"prompts/accounts/{key}.md"
            (self.root / filename).write_text("Ask a useful workflow question.")
            rows.append({"id":key,"upload_post_profile":profile,"platform":platform,
                         "native_account_id":"" if missing_id else "x-1" if platform=="x" else "t-1",
                         "handle":"builder" if platform=="x" else "disanchor","role":"builder" if platform=="x" else "brand",
                         "language":"en","approach":"Useful workflow observations","voice":"Curious and factual",
                         "prompt_file":filename,"reviewed":reviewed})
        content=[]
        for row in rows:
            content.append("[[accounts]]")
            content.extend(f"{key} = {json.dumps(value)}" for key,value in row.items())
        path=self.root / "accounts.toml"
        path.write_text("\n".join(content))
        return AccountRegistry(path)

    def publisher(self, **kwargs):
        return AccountBoundPublisher(KEY,self.registry(**kwargs),self.targets,self.transport)

    def test_constructor_requires_exact_supported_matching_targets(self):
        registry=self.registry()
        for targets in [{"x":"founder-x"}, {**self.targets,"instagram":"brand-threads"},
                        {"x":"brand-threads","threads":"founder-x"},
                        {"x":"missing","threads":"brand-threads"},
                        {"x":None,"threads":"brand-threads"}]:
            with self.assertRaises(ValidationError):
                AccountBoundPublisher(KEY,registry,targets,self.transport)
        self.assertEqual(self.transport.calls,[])

    def test_verify_returns_safe_binding_and_receipt_only(self):
        publisher=self.publisher()
        result=publisher.verify_destination("x")
        self.assertEqual(result["binding"]["id"],"founder-x")
        self.assertEqual(result["binding"]["upload_post_profile"],"builder")
        self.assertEqual(result["receipt"]["native_account_id"],"x-1")
        self.assertEqual([c[0] for c in self.transport.calls],["GET"])
        self.assertNotIn(KEY,json.dumps(result))

    def test_separate_platform_profiles_route_to_correct_destination_after_get(self):
        publisher=self.publisher()
        for platform,profile in [("x","builder"),("threads","brand")]:
            result=publisher.schedule(platform,"A useful workflow question.",self.when,"id-"+platform)
            before,post=self.transport.calls[-2:]
            self.assertEqual(before[0],"GET")
            self.assertTrue(before[1].endswith("/users/"+profile))
            self.assertEqual(post[0],"POST")
            self.assertEqual(post[3]["user"],[profile])
            self.assertEqual(post[3]["platform[]"],[platform])
            self.assertEqual(result["provider_job_id"],profile+"-"+platform+"-job")

    def test_reverify_catches_drift_after_prior_success_no_post(self):
        publisher=self.publisher()
        publisher.verify_destination("x")
        self.transport.accounts["builder"]["x"]["username"]="changed-account"
        with self.assertRaises(ValidationError) as caught:
            publisher.schedule("x","A useful question.",self.when,"id-x")
        self.assertEqual(caught.exception.code,"account_identity_changed")
        self.assertEqual([call[0] for call in self.transport.calls],["GET","GET"])

    def test_unreviewed_or_missing_identity_block_before_get_or_post(self):
        for kwargs in [{"reviewed":False},{"missing_id":True}]:
            publisher=self.publisher(**kwargs)
            with self.assertRaisesRegex(ValidationError,"account_identity_changed"):
                publisher.schedule("x","A useful question.",self.when,"id-x")
        self.assertEqual(self.transport.calls,[])

    def test_reauth_missing_and_unverified_identity_block_post(self):
        for value in [None, {"username":"x-1","reauth_required":True},
                      {"username":"x-1"}, {"display_name":"x-1","reauth_required":False}]:
            self.transport.accounts["builder"]["x"]=value
            publisher=self.publisher()
            with self.assertRaisesRegex(ValidationError,"account_identity_changed"):
                publisher.schedule("x","A useful question.",self.when,"id-x")
        self.assertTrue(all(call[0]=="GET" for call in self.transport.calls))

    def test_invalid_post_is_rejected_before_connection_lookup(self):
        publisher=self.publisher()
        for platform,text,when in [("x","https://example.test",self.when),
                                   ("instagram","text",self.when), ("threads","text","bad-time")]:
            with self.assertRaises(ValidationError):
                publisher.schedule(platform,text,when,"id-x")
        self.assertEqual(self.transport.calls,[])

    def test_http_auth_and_rate_limits_keep_safe_typed_errors(self):
        publisher=self.publisher()
        for status,expected in [(401,AuthenticationError),(429,RateLimitError)]:
            self.transport.failure=status
            with self.assertRaises(expected) as caught:
                publisher.schedule("x","A useful question.",self.when,"id-x")
            self.assertNotIn(KEY,str(caught.exception))
        self.assertTrue(all(call[0]=="GET" for call in self.transport.calls))

    def test_quota_is_one_api_account_lookup_not_sum_per_profile(self):
        result=self.publisher().quota()
        self.assertEqual(result["remaining"],7)
        self.assertEqual(result["scope"],"api_account")
        self.assertEqual(len(self.transport.calls),1)
        self.assertTrue(self.transport.calls[0][1].endswith("/uploadposts/me"))
        self.assertNotIn(KEY,json.dumps(result))

    def test_status_and_cancel_remain_available_without_current_binding_review(self):
        publisher=self.publisher(reviewed=False)
        self.assertEqual(publisher.status("accepted-job")["status"],"pending")
        self.assertEqual(publisher.cancel("accepted-job")["status"],"canceled")
        self.assertEqual([call[0] for call in self.transport.calls],["GET","DELETE"])
        self.assertTrue(all("/users/" not in call[1] for call in self.transport.calls))

    def test_analytics_requires_explicit_request_ids(self):
        publisher=self.publisher()
        with self.assertRaisesRegex(ValidationError,"bound_analytics_requires_post_request_ids"):
            publisher.analytics()
        result=publisher.analytics(["accepted-request"])
        self.assertEqual(result["scope"],"post")
        self.assertEqual(result["posts"][0]["metrics"]["views"],12)
        self.assertEqual(len(self.transport.calls),1)
        self.assertIn("/post-analytics/accepted-request",self.transport.calls[0][1])

    def test_history_queries_unique_profiles_and_filters_other_destinations(self):
        publisher=self.publisher()
        row={"profile_username":"builder","platform":"x","job_id":"j-x","status":"published"}
        self.transport.history={"builder":[row,copy.deepcopy(row),
                                            {"profile_username":"builder","platform":"threads","job_id":"not-selected"},
                                            {"profile_username":"other","platform":"x","job_id":"other-profile"},
                                            {"profile_username":"builder","platform":"instagram","job_id":"unsupported"}],
                                "brand":[{"profile_username":"brand","platform":"threads","job_id":"j-t"}]}
        results=publisher.history()
        self.assertEqual([row["provider_job_id"] for row in results],["j-x","j-t"])
        self.assertEqual([row["profile_username"] for row in results],["builder","brand"])
        self.assertEqual(len(self.transport.calls),2)

    def test_history_shared_profile_only_requested_once(self):
        publisher=self.publisher(shared=True)
        self.transport.accounts["builder"]["threads"]={"username":"t-1","reauth_required":False}
        self.transport.history["builder"]=[{"platform":"x","job_id":"shared-job"},
                                           {"platform":"threads","job_id":"shared-job"}]
        self.assertEqual(len(publisher.history()),2)
        self.assertEqual(len(self.transport.calls),1)

    def test_conflicting_history_identity_does_not_silently_choose_status(self):
        publisher=self.publisher()
        self.transport.history["builder"]=[{"platform":"x","job_id":"j-x","status":"published"},
                                           {"platform":"x","job_id":"j-x","status":"failed"}]
        with self.assertRaisesRegex(ProtocolError,"conflicting_account_history"):
            publisher.history()

    def test_registry_snapshot_changes_block_even_before_provider_call(self):
        publisher=self.publisher()
        publisher.registry._records["founder-x"]["native_account_id"]="changed-local-id"
        with self.assertRaisesRegex(ValidationError,"account_identity_changed"):
            publisher.verify_destination("x")
        self.assertEqual(self.transport.calls,[])


if __name__ == "__main__":
    unittest.main()
