import copy
import json
import os
import unittest
from unittest.mock import patch

from gtm.account_connection import UploadPostAccountClient, check_connection
from gtm.publisher import ProtocolError


KEY = "unit-test-provider-key-do-not-output"


def account(identity="x-123", handle="founder", **extra):
    return {"username": identity, "handle": handle, "display_name": "Founder", "reauth_required": False, **extra}


def profile(name="disanchor", accounts=None):
    return {"username": name, "display_name": "Dashboard label, never identity",
            "social_accounts": {"x": account(), "threads": account("threads-456", "studio")}
            if accounts is None else accounts}


def binding(platform="x", identity="x-123", name="disanchor", id="founder-x"):
    return {"id": id, "upload_post_profile": name, "platform": platform, "native_account_id": identity}


class Transport:
    def __init__(self):
        self.calls = []
        self.inventory = {"success": True, "limit": 999, "plan": "Business", "profiles": [profile()]}
        self.user = {"success": True, "email": "private@example.test", "message": KEY, "plan": "Business"}
        self.single = {"success": True, "profile": profile()}
        self.failure = None

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if self.failure is not None:
            if isinstance(self.failure, BaseException):
                raise self.failure
            return self.failure
        path = url.removeprefix("https://api.upload-post.com/api")
        if path == "/uploadposts/users":
            payload = self.inventory
        elif path == "/uploadposts/me":
            payload = self.user
        elif path.startswith("/uploadposts/users/"):
            payload = self.single
        else:
            raise AssertionError("unexpected endpoint")
        return {"status_code": 200, "headers": {}, "body": copy.deepcopy(payload)}


class AccountClientTests(unittest.TestCase):
    def setUp(self):
        self.transport = Transport()
        self.client = UploadPostAccountClient(KEY, self.transport)

    def test_inventory_separates_profile_name_native_ids_and_handles(self):
        rows = self.client.profiles()
        indexed = {row["platform"]: row for row in rows}
        self.assertEqual(indexed["x"]["profile_username"], "disanchor")
        self.assertEqual(indexed["x"]["native_account_id"], "x-123")
        self.assertEqual(indexed["x"]["handle"], "founder")
        self.assertEqual(indexed["threads"]["native_account_id"], "threads-456")
        self.assertEqual(indexed["threads"]["connection_state"], "connected")
        self.assertEqual(self.transport.calls[0][0], "GET")
        self.assertIsNone(self.transport.calls[0][3])

    def test_list_omitted_ids_are_hydrated_from_matching_single_profile(self):
        self.transport.inventory["profiles"][0]["social_accounts"]["x"].pop("username")
        self.transport.inventory["profiles"][0]["social_accounts"]["threads"].pop("username")
        rows = self.client.profiles()
        self.assertEqual({row["platform"]:row["native_account_id"] for row in rows}, {"x":"x-123", "threads":"threads-456"})
        self.assertEqual(len(self.transport.calls),2)
        self.assertTrue(self.transport.calls[-1][1].endswith("/uploadposts/users/disanchor"))
        self.assertTrue(all(call[0]=="GET" for call in self.transport.calls))

    def test_hydration_does_not_accept_another_profiles_identity(self):
        self.transport.inventory["profiles"][0]["social_accounts"]["x"].pop("username")
        self.transport.single["profile"]["username"]="different-profile"
        with self.assertRaisesRegex(ValueError,"binding_profile_mismatch"):
            self.client.profiles()

    def test_hydration_requests_are_bounded_before_any_detail_io(self):
        self.transport.inventory["profiles"]=[profile("profile-"+str(index), {"x":{"handle":"public-name","reauth_required":False}}) for index in range(33)]
        with self.assertRaisesRegex(ValueError,"profile_detail_limit_exceeded"):
            self.client.profiles()
        self.assertEqual(len(self.transport.calls),1)

    def test_integer_native_ids_preserve_all_digits_and_bool_is_not_id(self):
        identifier=1840000000000000001  # Synthetic 19-digit fixture, not a connected account.
        self.transport.single["profile"]["social_accounts"]["x"]["username"]=identifier
        row=self.client.verify_binding("disanchor","x",str(identifier))
        self.assertEqual(row["native_account_id"],str(identifier))
        for bad in [True, False, 0, -1, 1.5, 10**200]:
            self.transport.single["profile"]["social_accounts"]["x"]["username"]=bad
            with self.assertRaisesRegex(ValueError,"binding_identity_unverified"):
                self.client.verify_binding("disanchor","x",str(identifier))

    def test_empty_and_unrecognized_connections_never_become_connected(self):
        for value, expected in [(None,"missing"), ("","missing"), ("legacy details","identity_unverified"),
                                ({"display_name":"x-123"},"identity_unverified"),
                                (account(reauth_required=None),"authentication_unverified"),
                                (account(reauth_required=0),"authentication_unverified"),
                                (account(reauth_required="false"),"authentication_unverified"),
                                (account(reauth_required=True),"reauth_required")]:
            self.transport.inventory["profiles"] = [profile(accounts={"x":value})]
            self.transport.single = {"success": True, "profile": profile(accounts={"x":value})}
            row = next(row for row in self.client.profiles() if row["platform"] == "x")
            self.assertEqual(row["connection_state"], expected, value)
        missing = account()
        missing.pop("reauth_required")
        self.transport.inventory["profiles"] = [profile(accounts={"x":missing})]
        self.assertEqual(self.client.profiles()[-1]["connection_state"], "authentication_unverified")

    def test_missing_supported_platforms_are_explicit_rows(self):
        self.transport.inventory["profiles"] = [profile(accounts={})]
        rows = self.client.profiles()
        self.assertEqual({row["platform"] for row in rows}, {"x", "threads"})
        self.assertTrue(all(row["connection_state"] == "missing" for row in rows))

    def test_other_known_channels_are_observed_without_publish_support(self):
        self.transport.inventory["profiles"] = [profile(accounts={"instagram":account("ig-1"), KEY:account()})]
        rows = self.client.profiles()
        instagram = next(row for row in rows if row["platform"] == "instagram")
        self.assertFalse(instagram["supported"])
        self.assertEqual(instagram["connection_state"], "connected")
        self.assertNotIn(KEY, json.dumps(rows))
        with self.assertRaises(ValueError):
            self.client.verify_binding("disanchor", "instagram", "ig-1")
        self.assertEqual(len(self.transport.calls), 1)

    def test_tokens_images_contact_and_extra_provider_fields_omitted(self):
        self.transport.inventory["profiles"][0]["social_accounts"]["x"].update(
            access_token=KEY, refresh_token=KEY, social_images="https://private.test/"+KEY, email="private@example.test")
        serialized = json.dumps(self.client.profiles())
        for text in [KEY, "access_token", "refresh_token", "social_images", "private@example.test"]:
            self.assertNotIn(text, serialized)
        self.transport.inventory["profiles"][0]["social_accounts"]["x"].update(username=KEY, handle=KEY, display_name=KEY)
        rows=self.client.profiles()
        x=next(row for row in rows if row["platform"]=="x")
        self.assertIsNone(x["native_account_id"])
        self.assertIsNone(x["handle"])
        self.assertIsNone(x["display_name"])
        self.assertNotIn(KEY, json.dumps(rows))

    def test_duplicate_profile_and_bad_shapes_fail_closed(self):
        bad = [{"success": "true", "profiles": []}, {"success": True, "profiles": {}},
               {"success": True, "profiles": [profile(), profile()]},
               {"success": True, "profiles": [{"username":"disanchor"}]},
               {"success": True, "profiles": [profile("../wrong")]},
               {"success": True, "profiles": [None]}]
        for body in bad:
            self.transport.inventory=body
            with self.assertRaises(ValueError):
                self.client.profiles()
        self.transport.inventory = {"success": True, "profiles": [profile()] * 1001}
        with self.assertRaises(ValueError):
            self.client.profiles()

    def test_quota_is_unknown_without_documented_usage_fields(self):
        user=self.client.current_user()
        self.assertEqual(user["scope"], "api_account")
        self.assertEqual(user["plan"], "business")
        self.assertIsNone(user["remaining"])
        self.assertFalse(user["quota_verified"])
        self.assertNotIn("private@example.test", json.dumps(user))
        self.assertNotIn(KEY, json.dumps(user))

    def test_quota_numbers_strict_and_not_inferred_from_plan_or_profile_limit(self):
        for usage, expected in [({"count":3,"limit":10},7), ({"count":12,"limit":10},0),
                                ({"count":True,"limit":10},None), ({"count":3.0,"limit":10},None),
                                ({"count":3},None), ({"count":-1,"limit":10},None),
                                ({"count":0,"limit":False},None)]:
            self.transport.user["api_usage"]=usage
            self.assertEqual(self.client.current_user()["remaining"],expected)
        self.transport.user.pop("api_usage")
        self.transport.user["usage"]={"count":0,"limit":999}
        self.assertIsNone(self.client.current_user()["remaining"])

    def test_single_profile_path_encoded_and_returned_identity_checked(self):
        self.transport.single["profile"]["username"]="founder@example"
        row=self.client.verify_binding("founder@example", "x", "x-123")
        self.assertEqual(row["profile_username"],"founder@example")
        self.assertTrue(self.transport.calls[-1][1].endswith("/uploadposts/users/founder%40example"))
        self.transport.single["profile"]["username"]="someone-else"
        with self.assertRaisesRegex(ValueError,"binding_profile_mismatch"):
            self.client.verify_binding("founder@example","x","x-123")

    def test_changed_native_id_is_blocked_even_when_handle_is_same(self):
        self.transport.single["profile"]["social_accounts"]["x"]["username"]="another-account"
        with self.assertRaisesRegex(ValueError,"binding_account_mismatch"):
            self.client.verify_binding("disanchor","x","x-123")

    def test_handle_rename_preserves_identity_and_shows_new_label(self):
        self.transport.single["profile"]["social_accounts"]["x"]["handle"]="@new_handle"
        self.assertEqual(self.client.verify_binding("disanchor","x","x-123")["handle"],"new_handle")

    def test_verify_requires_explicit_id_and_authentication(self):
        for value, reason in [(None,"binding_connection_missing"),
                              ({"display_name":"x-123"},"binding_identity_unverified"),
                              (account(reauth_required=True),"binding_reauthentication_required"),
                              (account(reauth_required=None),"binding_authentication_unverified")]:
            self.transport.single["profile"]["social_accounts"]["x"]=value
            with self.assertRaisesRegex(ValueError,reason):
                self.client.verify_binding("disanchor","x","x-123")

    def test_malformed_binding_inputs_fail_before_request(self):
        for args in [("../profile","x","x-123"), ("disanchor",[],"x-123"),
                     ("disanchor","x",None), ("disanchor","x","id\nsecret")]:
            with self.assertRaises(ValueError):
                self.client.verify_binding(*args)
        self.assertEqual(self.transport.calls,[])


class ConnectionCheckTests(unittest.TestCase):
    def setUp(self):
        self.transport=Transport()

    def test_missing_key_zero_calls_and_no_implicit_environment_loading(self):
        with patch.dict(os.environ,{"UPLOAD_POST_API_KEY":KEY}):
            result=check_connection(transport=self.transport)
        self.assertEqual(result["status"],"disabled")
        self.assertEqual(result["network_calls"],0)
        self.assertEqual(result["posts_sent"],0)
        self.assertEqual(self.transport.calls,[])

    def test_inventory_without_bindings_does_not_select_or_authorize_account(self):
        result=check_connection(KEY,transport=self.transport)
        self.assertEqual(result["status"],"needs_binding")
        self.assertEqual(result["network_calls"],2)
        self.assertEqual(result["bindings"],[])
        self.assertIsNone(result["quota"]["remaining"])
        self.assertEqual([c[0] for c in self.transport.calls],["GET","GET"])
        self.assertTrue(all(c[3] is None for c in self.transport.calls))

    def test_verified_two_channels_are_read_only_and_not_quota_approval(self):
        result=check_connection(KEY,[binding(),binding("threads","threads-456",id="brand-threads")],self.transport)
        self.assertEqual(result["status"],"verified_read_only")
        self.assertEqual(result["network_calls"],4)
        self.assertEqual(len(result["bindings"]),2)
        self.assertEqual(result["posts_sent"],0)
        self.assertIsNone(result["quota"]["remaining"])
        self.assertTrue(all(call[0]=="GET" for call in self.transport.calls))
        self.assertTrue(all(call[2]["Authorization"]=="Apikey "+KEY for call in self.transport.calls))
        self.assertNotIn(KEY,json.dumps(result))

    def test_duplicate_binding_ids_or_native_destination_fail_before_io(self):
        for bindings in [[binding(),binding(id="other-profile")],
                         [binding(),binding("threads","threads-456",id="founder-x")],
                         [binding(),binding(name="other-profile",id="other")]]:
            result=check_connection(KEY,bindings,self.transport)
            self.assertEqual(result["status"],"blocked")
            self.assertEqual(result["reason"],"duplicate_binding_identity")
            self.assertEqual(result["network_calls"],0)
        self.assertEqual(self.transport.calls,[])

    def test_errors_and_provider_message_bodies_never_escape(self):
        for status, reason in [(401,"authentication_failed"),(403,"permission_or_plan_denied"),
                               (404,"profile_not_found"),(429,"rate_limited"),(500,"provider_unavailable")]:
            self.transport.failure={"status_code":status,"headers":{},"body":{"message":KEY,"success":False}}
            result=check_connection(KEY,transport=self.transport)
            self.assertEqual(result["reason"],reason)
            self.assertEqual(result["status"],"blocked")
            self.assertEqual(result["posts_sent"],0)
            self.assertNotIn(KEY,json.dumps(result))
        self.transport.failure=TimeoutError(KEY)
        result=check_connection(KEY,transport=self.transport)
        self.assertEqual(result["reason"],"provider_unavailable")
        self.assertNotIn(KEY,json.dumps(result))

    def test_success_false_is_not_authentication_confirmation(self):
        self.transport.user={"success":False,"message":KEY}
        result=check_connection(KEY,transport=self.transport)
        self.assertEqual(result["status"],"blocked")
        self.assertIsNone(result["quota"])
        self.assertNotIn(KEY,json.dumps(result))

    def test_failed_binding_keeps_overall_blocked_and_posts_zero(self):
        result=check_connection(KEY,[binding(identity="wrong-account")],self.transport)
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(result["reason"],"binding_account_mismatch")
        self.assertEqual(result["posts_sent"],0)
        self.assertEqual(result["bindings"],[])

    def test_bad_bindings_and_secret_in_identifier_never_echo(self):
        bad=[binding(id=KEY),binding(platform=[]),{"id":"private","other":KEY}]
        for value in bad:
            result=check_connection(KEY,[value],self.transport)
            self.assertEqual(result["status"],"blocked")
            self.assertEqual(result["network_calls"],0)
            self.assertNotIn(KEY,json.dumps(result))

    def test_empty_inventory_is_not_a_successful_binding(self):
        self.transport.inventory["profiles"]=[]
        result=check_connection(KEY,transport=self.transport)
        self.assertEqual(result["status"],"needs_binding")
        self.assertEqual(result["inventory"],[])


if __name__ == "__main__":
    unittest.main()
