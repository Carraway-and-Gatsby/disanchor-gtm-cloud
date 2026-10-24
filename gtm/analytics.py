"""Read-only GA4 aggregate reporting. No user-level dimensions or write APIs."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
import re
import urllib.error
import urllib.request

METRICS = {"users": "activeUsers", "sessions": "sessions", "engaged_sessions": "engagedSessions"}
EVENTS = ("page_view", "cta_click", "lead_form_start", "view_research_form", "generate_lead")
SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}\Z")
KEY_EVENT_RATE = "sessionKeyEventRate:generate_lead"
EXPERIMENT_CONTENT_IDS = frozenset(["x_profile"] + [f"threads_{n:02}" for n in range(1, 6)])


def approved_content_id(value):
    return isinstance(value, str) and (value in EXPERIMENT_CONTENT_IDS or bool(re.fullmatch(r"c_[a-z0-9][a-z0-9_-]{0,63}", value)))


def _period(start_date, end_date):
    start, end = dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


def _empty_metrics():
    return {**dict.fromkeys(METRICS), "event_counts": dict.fromkeys(EVENTS)}


class ReportUnavailable(Exception):
    """Contains only a safe reason code, never raw API response text."""


class GA4Client:
    """transport(url, body_dict, headers_dict) -> decoded runReport dict.

    Supply an OAuth access token, or use from_service_account for automatic
    refresh. The client never changes GA4 configuration or sends events.
    """
    simulated = False

    def __init__(self, property_id, access_token, transport=None, *, include_content=False,
                 key_event_registered=False, token_provider=None):
        if not re.fullmatch(r"[1-9][0-9]*", str(property_id)):
            raise ValueError("numeric GA4 property_id required")
        self.property_id = str(property_id)
        self.access_token = access_token
        self.transport = transport or self._http
        self.include_content = include_content
        if type(key_event_registered) is not bool:
            raise ValueError("key_event_registered must be an explicit boolean")
        if token_provider is not None and not callable(token_provider):
            raise ValueError("token_provider must be callable")
        self.key_event_registered = key_event_registered
        self.token_provider = token_provider
        self._auth_failed = False
        self._collection_block = None

    @classmethod
    def from_service_account(cls, property_id, credentials_file=None, transport=None, **kwargs):
        """Optional google-auth[requests] adapter, no network until collect().

        credentials_file defaults to GOOGLE_APPLICATION_CREDENTIALS. The service
        account must already have read access to the configured GA4 property.
        Credentials are never printed, copied to reports or stored in SQLite.
        """
        filename = credentials_file or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not filename:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS is not configured")
        try:
            from google.oauth2.service_account import Credentials
            from google.auth.transport.requests import Request
        except ImportError:
            raise ValueError("install optional google-auth[requests]>=2,<3") from None
        try:
            credentials = Credentials.from_service_account_file(filename, scopes=["https://www.googleapis.com/auth/analytics.readonly"])
            request = Request()
        except Exception:
            raise ValueError("service_account_configuration_invalid") from None

        def bounded_request(*args, **request_kwargs):
            request_kwargs["timeout"] = 20
            return request(*args, **request_kwargs)

        def provide_token():
            # Google's before_request refreshes only when credentials need it.
            credentials.before_request(bounded_request, "POST", "https://analyticsdata.googleapis.com/", {})
            return credentials.token

        return cls(property_id, "", transport, token_provider=provide_token, **kwargs)

    def _headers(self):
        if self._auth_failed:
            raise ReportUnavailable("credentials_refresh_failed")
        try:
            token = self.token_provider() if self.token_provider else self.access_token
            if not isinstance(token, str) or not token.strip() or "\n" in token or "\r" in token:
                raise ValueError("invalid token")
            return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        except Exception:
            self._auth_failed = True
            raise ReportUnavailable("credentials_refresh_failed") from None

    @staticmethod
    def _http(url, body, headers):
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            # Refuse redirects so a bearer token cannot follow a changed host.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                    return None
            with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
                data = response.read(8_000_001)
                if len(data) > 8_000_000:
                    raise ReportUnavailable("response_too_large")
                return json.loads(data)
        except urllib.error.HTTPError as error:
            raise ReportUnavailable("http_" + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise ReportUnavailable("transport_failure") from None

    def _report(self, period, campaign, dimensions, metrics):
        rows, warnings, metadata = [], [], {}
        for page in range(4):
            body = {
                "dateRanges": [{"startDate": period["start_date"], "endDate": period["end_date"]}],
                "dimensions": [{"name": item} for item in dimensions],
                "metrics": [{"name": item} for item in metrics],
                "dimensionFilter": {"filter": {"fieldName": "sessionCampaignName", "stringFilter": {"matchType": "EXACT", "value": campaign, "caseSensitive": True}}},
                "limit": "25000", "offset": str(len(rows)), "returnPropertyQuota": True,
                "orderBys": [{"dimension": {"dimensionName": name}} for name in dimensions],
            }
            payload = self.transport("https://analyticsdata.googleapis.com/v1beta/properties/" + self.property_id + ":runReport", body, self._headers())
            if not isinstance(payload, dict) or "error" in payload:
                raise ReportUnavailable("invalid_api_response")
            metadata.update(payload.get("metadata", {}))
            for key in ("subjectToThresholding", "dataLossFromOtherRow", "samplingMetadatas", "dataTruncationReasons"):
                if metadata.get(key):
                    warnings.append(key)
            if metadata.get("emptyReason"):
                raise ReportUnavailable("ga4_empty_reason")
            active_restrictions = metadata.get("schemaRestrictionResponse", {}).get("activeMetricRestrictions", [])
            restricted = {item.get("metricName") for item in active_restrictions}
            dim_names = [item["name"] for item in payload.get("dimensionHeaders", [])]
            metric_names = [item["name"] for item in payload.get("metricHeaders", [])]
            if any(name not in dim_names for name in dimensions):
                raise ReportUnavailable("missing_dimension_header")
            batch = payload.get("rows", [])
            for raw in batch:
                row = {name: value.get("value", "") for name, value in zip(dim_names, raw.get("dimensionValues", []))}
                values = dict(zip(metric_names, raw.get("metricValues", [])))
                for name in metrics:
                    value = values.get(name, {}).get("value")
                    row[name] = None
                    if name not in restricted and isinstance(value, str):
                        if name == KEY_EVENT_RATE:
                            try:
                                parsed = float(value)
                                row[name] = parsed if math.isfinite(parsed) and 0 <= parsed <= 1 else None
                            except ValueError:
                                pass
                        elif re.fullmatch(r"[0-9]+", value):
                            row[name] = int(value)
                rows.append(row)
            count = int(payload.get("rowCount", len(rows)))
            if len(rows) >= count:
                return rows, sorted(set(warnings))
            if not batch:
                raise ReportUnavailable("incomplete_pagination")
        raise ReportUnavailable("row_limit_exceeded")

    def collect(self, start_date, end_date, campaign):
        """Return measured aggregates; unavailable values stay None with reasons.

        users means activeUsers reported by GA4. Counts respect the property's
        reporting identity; they are not an audited count of individual people.
        A standalone totals query avoids summing non-additive user buckets.
        """
        period = _period(start_date, end_date)
        period["timezone"] = "GA4 property timezone"
        if not isinstance(campaign, str) or not SLUG.fullmatch(campaign):
            raise ValueError("campaign must be a non-personal lowercase campaign slug")
        result = {"source": "ga4", "mode": "live", "campaign": campaign, "period": period,
                  "totals": _empty_metrics(), "channels": None, "content": None,
                  "key_event_session_rate": None, "key_event_sessions": None,
                  "missing": {}, "warnings": [], "users_definition": "activeUsers",
                  "channel_dimensions": ["sessionSource", "sessionMedium"]}
        self._auth_failed = False
        self._collection_block = None
        if not self.token_provider and (not isinstance(self.access_token, str) or not self.access_token.strip()):
            result["missing"]["all"] = "not_configured"
            return result

        def read(key, dims, metrics):
            if self._collection_block:
                result["missing"][key] = self._collection_block
                return None, False
            try:
                rows, warnings = self._report(period, campaign, dims, metrics)
                result["warnings"].extend(key + ":" + warning for warning in warnings)
                if not rows:
                    result["missing"][key] = "no_rows_returned"
                for name in metrics:
                    if any(row.get(name) is None for row in rows):
                        result["missing"][key + "." + name] = "missing_or_restricted_metric"
                if warnings:
                    result["missing"][key + ".unreturned_values"] = "report_quality_limits; see warnings"
                return rows, not warnings and all(row.get(name) is not None for row in rows for name in metrics)
            except ReportUnavailable as error:
                result["missing"][key] = str(error)
                if str(error) in {"http_401", "http_403", "http_429", "credentials_refresh_failed"}:
                    self._collection_block = str(error)
            except Exception:
                # Injected transports must not leak credentials or response bodies.
                result["missing"][key] = "report_failure"
            return None, False

        def events_for(rows, complete):
            if rows is None or not rows:
                return dict.fromkeys(EVENTS)
            events = {event: (0 if complete else None) for event in EVENTS}
            for row in rows:
                if row.get("eventName") in EVENTS:
                    events[row["eventName"]] = row.get("eventCount")
            return events

        rows, _ = read("totals", [], list(METRICS.values()))
        if rows and len(rows) == 1:
            result["totals"].update({key: rows[0].get(metric) for key, metric in METRICS.items()})
        elif rows:
            result["missing"]["totals"] = "unexpected_multiple_total_rows"
        events, complete = read("events", ["eventName"], ["eventCount"])
        result["totals"]["event_counts"] = events_for(events, complete)
        if self.key_event_registered:
            rate_rows, _ = read("key_event_session_rate", [], [KEY_EVENT_RATE])
            if rate_rows and len(rate_rows) == 1:
                result["key_event_session_rate"] = rate_rows[0].get(KEY_EVENT_RATE)
            elif rate_rows:
                result["missing"]["key_event_session_rate"] = "unexpected_multiple_total_rows"
            result["warnings"].append("key_event_registration_is_not_retroactive; verify registration predates reporting period")
        else:
            result["missing"]["key_event_session_rate"] = "generate_lead_key_event_registration_not_verified"
        # A rounded rate times sessions is not an independently observed count.
        result["missing"]["key_event_sessions"] = "not_independently_queried; do_not_derive_from_eventCount_or_rounded_rate"

        for key, dimensions in (("channels", ["sessionSource", "sessionMedium"]), ("content", ["sessionManualAdContent"])):
            if key == "content" and not self.include_content:
                result["missing"]["content"] = "not_requested"
                continue
            grouped, _ = read(key, dimensions, list(METRICS.values()))
            grouped_events, complete = read(key + "_events", dimensions + ["eventName"], ["eventCount"])
            if grouped is None:
                continue
            result[key] = []
            for row in grouped:
                values = tuple(row.get(dimension, "") for dimension in dimensions)
                # Raw URL/user-created strings never enter downstream reporting.
                safe = values in (("x", "organic_social"), ("threads", "organic_social")) if key == "channels" else approved_content_id(values[0])
                if not safe:
                    result["warnings"].append(key + ":unsafe_or_unassigned_label_omitted")
                    continue
                matching = None if grouped_events is None else [r for r in grouped_events if tuple(r.get(d) for d in dimensions) == values]
                labels = {"source": values[0], "medium": values[1]} if key == "channels" else {"content_id": values[0]}
                result[key].append({**labels, **{k: row.get(v) for k, v in METRICS.items()}, "event_counts": events_for(matching, complete)})
        result["warnings"] = sorted(set(result["warnings"]))
        return result


class MockGA4:
    """Deterministic synthetic aggregate fixture; explicitly marked mock."""
    simulated = True
    def __init__(self, fixture=None):
        self.fixture = fixture

    def collect(self, start_date, end_date, campaign):
        if self.fixture is not None:
            result = copy.deepcopy(self.fixture)
            result["mode"] = "mock"
            return result
        return {"source": "ga4", "mode": "mock", "campaign": campaign,
                "period": _period(start_date, end_date), "users_definition": "activeUsers",
                "totals": {"users": 12, "sessions": 16, "engaged_sessions": 9, "event_counts": {"page_view": 20, "cta_click": 7, "lead_form_start": 4, "view_research_form": 5, "generate_lead": 2}},
                "channels": [{"source": "x", "medium": "organic_social", "users": 7, "sessions": 9, "engaged_sessions": 5, "event_counts": {"page_view": 12, "cta_click": 4, "lead_form_start": 2, "view_research_form": 3, "generate_lead": 1}},
                             {"source": "threads", "medium": "organic_social", "users": 6, "sessions": 7, "engaged_sessions": 4, "event_counts": {"page_view": 8, "cta_click": 3, "lead_form_start": 2, "view_research_form": 2, "generate_lead": 1}}],
                "channel_dimensions": ["sessionSource", "sessionMedium"],
                "key_event_session_rate": None, "key_event_sessions": None,
                "content": None, "missing": {"content": "not_requested", "key_event_session_rate": "mock_registration_not_verified", "key_event_sessions": "not_independently_queried"}, "warnings": ["synthetic_test_data"]}
