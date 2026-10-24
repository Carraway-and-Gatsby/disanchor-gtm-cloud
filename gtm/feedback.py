"""Bounded, read-only own-post feedback with no retained comment text or identities.

Comments are untrusted data. They never become model instructions or skill text.
Only local English/Korean keyword counts leave this module; this is a weak
directional signal, not a psychological assessment or a representative survey.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re

from .publisher import UploadPostPublisher, PublisherError, ProtocolError, ValidationError
from .account_connection import UploadPostAccountClient

VERSION = "feedback-v1"
HOOKS = frozenset(("question", "situation", "observation", "tradeoff", "reflection",
                  "founder_insight", "first_principles"))
_KEY = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_NATIVE = re.compile(r"[0-9]{1,40}\Z")
_POS = re.compile(r"\b(?:good|useful|helpful|love|great|excellent|thanks|insightful|clear)\b|"
                  r"도움|유용|좋아|좋네|좋은|감사|공감|훌륭|명확", re.I)
_NEG = re.compile(r"\b(?:bad|useless|confusing|hate|misleading|spam|scam|terrible|frustrating|broken)\b|"
                  r"별로|불편|스팸|사기|싫|실망|헷갈|무의미|불필요", re.I)
_UNCERTAIN = re.compile(r"\b(?:not|no|never|isn['’]t|wasn['’]t|don['’]t|doesn['’]t|"
                        r"yeah\s+right|ignore\s+(?:all|previous)|system\s+prompt|api[_ -]?key)\b|"
                        r"/s\b|않|아닌|아니라|안\s+|지침|비밀번호", re.I)
_PRIVATE = re.compile(r"https?://\S+|www\.\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
                      r"(?<!\w)@[\w.]+|\+?\d[\d ()-]{7,}\d", re.I)
_LABELS = ("positive", "negative", "mixed", "unknown")


def _bound(value, minimum, maximum, name):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError("invalid_feedback_" + name)
    return value


def classify_sentiment(text):
    """Return a closed label only; ambiguity and unsupported language stay unknown."""
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 4096:
        return "unknown"
    text = _PRIVATE.sub(" ", text)
    if _UNCERTAIN.search(text):
        return "unknown"
    positive, negative = bool(_POS.search(text)), bool(_NEG.search(text))
    return "mixed" if positive and negative else "positive" if positive else "negative" if negative else "unknown"


def sentiment_summary(counts=None, *, min_comments=5):
    _bound(min_comments, 5, 50, "minimum_comments")
    if counts is None:
        return {"counts": None, "sample_size": None, "classified_count": None,
                "positive_share": None, "label": "unknown", "method": "keyword_en_ko_v1"}
    if (not isinstance(counts, dict) or set(counts) != set(_LABELS) or
            any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in counts.values())):
        raise ValueError("invalid_sentiment_counts")
    total = sum(counts.values())
    if total > 50:
        raise ValueError("sentiment_sample_exceeds_bound")
    classified = total - counts["unknown"]
    share, label = None, "unknown"
    if classified >= min_comments and classified >= total / 2:
        share = round(counts["positive"] / classified, 6)
        if counts["mixed"] or (counts["positive"] and counts["negative"]):
            label = "mixed"
        elif counts["positive"]:
            label = "positive"
        elif counts["negative"]:
            label = "negative"
    return {"counts": dict(counts), "sample_size": total, "classified_count": classified,
            "positive_share": share, "label": label, "method": "keyword_en_ko_v1"}


class UploadPostFeedbackClient:
    """Read only an explicit allowlist of already published local post identities.

    The host builds allowed_posts from its publication receipts and validates the
    current registry fingerprint. Constructor data cannot authorize publishing.
    No native post ID can be supplied dynamically to collect().
    """
    simulated = False

    def __init__(self, api_key, allowed_posts, transport=None, *, max_pages=2,
                 max_comments=40, min_comments=5):
        self.max_pages = _bound(max_pages, 1, 2, "pages")
        self.max_comments = _bound(max_comments, 1, 50, "comments")
        self.min_comments = _bound(min_comments, 5, 50, "minimum_comments")
        if not isinstance(allowed_posts, (list, tuple)) or len(allowed_posts) > 10:
            raise ValueError("feedback_requires_at_most_ten_own_posts")
        fields = {"post_id", "account_key", "account_fingerprint", "profile_username",
                  "platform", "platform_post_id", "native_account_id"}
        self._posts, self._publishers, seen, owners = {}, {}, set(), {}
        self._accounts = UploadPostAccountClient(api_key, transport)
        self._verified = set()
        for row in allowed_posts:
            if (not isinstance(row, dict) or set(row) != fields or
                    any(not isinstance(v, str) for v in row.values()) or
                    any(not _KEY.fullmatch(row[k]) for k in ("post_id", "account_key", "profile_username")) or
                    not _HASH.fullmatch(row["account_fingerprint"]) or row["platform"] not in {"x", "threads"} or
                    any(not _NATIVE.fullmatch(row[k]) for k in ("platform_post_id", "native_account_id"))):
                raise ValueError("invalid_feedback_own_post_binding")
            identity = (row["platform"], row["platform_post_id"])
            if row["post_id"] in self._posts or identity in seen:
                raise ValueError("duplicate_feedback_post_identity")
            owner_key = (row["profile_username"], row["platform"])
            if owner_key in owners and owners[owner_key] != row["native_account_id"]:
                raise ValueError("conflicting_feedback_owner_binding")
            owners[owner_key] = row["native_account_id"]
            seen.add(identity)
            self._posts[row["post_id"]] = dict(row)
            if row["profile_username"] not in self._publishers:
                self._publishers[row["profile_username"]] = UploadPostPublisher(api_key, row["profile_username"], transport)
        self.network_calls = 0

    def collect(self, post_id):
        if not isinstance(post_id, str) or post_id not in self._posts:
            raise ValidationError("feedback_post_not_in_published_allowlist")
        post = self._posts[post_id]
        result = {key: post[key] for key in ("post_id", "account_key", "account_fingerprint", "platform")}
        result.update({"status": "unavailable", "reason": "comments_unavailable", "pages": 0,
                       "network_calls": 0, "truncated": False, "partial": False,
                       "coverage": "mentions_sample" if post["platform"] == "x" else "reply_sample",
                       "excluded": 0, "sentiment": sentiment_summary(), "simulated": False})
        counts = dict.fromkeys(_LABELS, 0)
        seen, seen_texts, seen_cursors, cursor = set(), set(), set(), None
        sampled = 0
        try:
            owner_key = (post["profile_username"], post["platform"], post["native_account_id"])
            if owner_key not in self._verified:
                self.network_calls += 1
                result["network_calls"] += 1
                self._accounts.verify_binding(*owner_key)
                self._verified.add(owner_key)
            for page in range(self.max_pages):
                query = {"platform": post["platform"], "user": post["profile_username"],
                         "post_id": post["platform_post_id"], "limit": min(25, self.max_comments - sampled)}
                if cursor is not None:
                    query["after"] = cursor
                self.network_calls += 1
                result["network_calls"] += 1
                data = self._publishers[post["profile_username"]]._call("GET", "/uploadposts/comments", query=query)
                result["pages"] += 1
                if data.get("success") is not True or not isinstance(data.get("comments"), list):
                    raise ProtocolError("feedback_comments_schema_invalid")
                for key, expected in (("platform", post["platform"]), ("user", post["profile_username"]),
                                      ("profile_username", post["profile_username"]), ("post_id", post["platform_post_id"])):
                    if key in data and str(data[key]) != expected:
                        raise ProtocolError("feedback_response_identity_mismatch")
                if data.get("partial") is True:
                    result.update(status="partial", reason="read_replies_permission_partial", partial=True)
                    return result
                batch = data["comments"]
                # Even if a provider ignores limit, inspect no more than the fixed sample bound.
                for comment in batch[:self.max_comments - sampled]:
                    sampled += 1
                    if not isinstance(comment, dict):
                        raise ProtocolError("feedback_comment_schema_invalid")
                    cid = comment.get("id")
                    if not isinstance(cid, (str, int)) or isinstance(cid, bool) or not _NATIVE.fullmatch(str(cid)):
                        result["excluded"] += 1
                        continue
                    cid = str(cid)
                    if cid in seen:
                        result["excluded"] += 1
                        continue
                    seen.add(cid)
                    author = comment.get("user") if isinstance(comment.get("user"), dict) else {}
                    author_id = str(comment.get("author_id", author.get("id", "")))
                    owner_flag = comment.get("is_owner")
                    if owner_flag is True or author_id == post["native_account_id"]:
                        result["excluded"] += 1
                        continue
                    # Unknown authorship could be a first comment by the brand itself.
                    if owner_flag is not False and not _NATIVE.fullmatch(author_id):
                        result["excluded"] += 1
                        continue
                    raw_text = comment.get("text")
                    if isinstance(raw_text, str):
                        text_hash = hashlib.sha256(_PRIVATE.sub(" ", raw_text).strip().casefold().encode()).hexdigest()
                        if text_hash in seen_texts:
                            result["excluded"] += 1
                            continue
                        seen_texts.add(text_hash)
                    counts[classify_sentiment(raw_text)] += 1
                pagination = data.get("pagination", {})
                if not isinstance(pagination, dict) or ("has_next" in pagination and type(pagination["has_next"]) is not bool):
                    raise ProtocolError("feedback_pagination_schema_invalid")
                more = pagination.get("has_next", False)
                result["truncated"] = len(batch) > query["limit"] or more
                if not more or sampled >= self.max_comments:
                    break
                cursor = pagination.get("next_cursor")
                if (not isinstance(cursor, str) or not 0 < len(cursor) <= 1024 or
                        any(ord(c) < 32 for c in cursor) or cursor in seen_cursors):
                    raise ProtocolError("feedback_pagination_cursor_invalid")
                seen_cursors.add(cursor)
            result.update(status="sampled" if sum(counts.values()) else "empty", reason=None,
                          sentiment=sentiment_summary(counts, min_comments=self.min_comments))
        except PublisherError as exc:
            # Constant error codes only. No provider body, raw text, handle, ID or cursor survives.
            result["reason"] = "rate_limited" if exc.http_status == 429 else "comments_permission_or_provider_unavailable"
            result["sentiment"] = sentiment_summary()
        except (ValueError, TypeError, KeyError):
            result["reason"] = "account_identity_unverified_or_changed"
            result["sentiment"] = sentiment_summary()
        return result


def _time(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None


def _number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1_000_000_000_000 and math.isfinite(value):
        # Counts expressed as 100.0 are exact integers; never round fractional data.
        return int(value) if value == int(value) else value
    return None


def _identity(payload):
    if (not isinstance(payload, dict) or not isinstance(payload.get("platform"), str) or payload["platform"] not in {"x", "threads"} or
            any(not isinstance(payload.get(k), str) or not _KEY.fullmatch(payload[k]) for k in ("post_id", "account_key")) or
            not isinstance(payload.get("account_fingerprint"), str) or not _HASH.fullmatch(payload["account_fingerprint"])):
        return None
    return (payload["account_key"], payload["account_fingerprint"], payload["platform"], payload["post_id"])


def _latest(records, now):
    latest = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        identity = _identity(row.get("payload"))
        observed = _time(row.get("observed_at"))
        if identity is None or observed is None or observed > now or observed < now - timedelta(hours=48):
            continue
        if identity not in latest or observed > latest[identity][0]:
            latest[identity] = (observed, row["payload"])
        elif observed == latest[identity][0] and row["payload"] != latest[identity][1]:
            # Conflicting same-time snapshots cannot select a convenient result.
            latest[identity] = (observed, None)
    return {key: value for key, value in latest.items() if value[1] is not None}


def aggregate_feedback(metric_records, sentiment_records, *, now=None, minimum_posts=3,
                       minimum_exposures=100, min_comments=5):
    """Produce a bounded, identity-isolated learning envelope from latest snapshots.

    Input is Store.records() output. Exposure rates use likes + replies/comments
    + reposts/shares only when all three are known. Repeated cumulative snapshots
    are never summed. Results are directional, never a statistically proven win.
    """
    _bound(minimum_posts, 3, 10, "minimum_posts")
    _bound(minimum_exposures, 100, 1_000_000, "minimum_exposures")
    _bound(min_comments, 5, 50, "minimum_comments")
    now = _time(now or datetime.now(timezone.utc))
    if now is None:
        raise ValueError("feedback_now_requires_timezone")
    if not isinstance(metric_records, (list, tuple)) or not isinstance(sentiment_records, (list, tuple)):
        raise ValueError("feedback_requires_snapshot_lists")
    metrics, comments = _latest(metric_records, now), _latest(sentiment_records, now)
    posts, grouped = [], defaultdict(list)
    # The experiment has ten slots. Newest records take priority if imported data exceeds it.
    for identity, (observed, payload) in sorted(metrics.items(), key=lambda item: item[1][0], reverse=True)[:10]:
        account, fingerprint, platform, post_id = identity
        values = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        denominator = next((key for key in ("impressions", "views", "reach") if _number(values.get(key)) is not None), None)
        exposures = _number(values.get(denominator)) if denominator else None
        likes = _number(values.get("likes"))
        replies = next((_number(values.get(key)) for key in ("replies", "comments") if _number(values.get(key)) is not None), None)
        reposts = next((_number(values.get(key)) for key in ("reposts", "shares") if _number(values.get(key)) is not None), None)
        engagements = sum((likes, replies, reposts)) if all(v is not None for v in (likes, replies, reposts)) else None
        rates = engagements / exposures if engagements is not None and exposures is not None and exposures > 0 else None
        comment = comments.get(identity)
        summary = sentiment_summary()
        if comment and comment[1].get("status") in {"sampled", "empty"} and comment[1].get("partial") is False:
            supplied = comment[1].get("sentiment")
            try:
                summary = sentiment_summary(supplied.get("counts") if isinstance(supplied, dict) else None, min_comments=min_comments)
            except ValueError:
                pass
        if not any(_number(values.get(key)) is not None for key in
                   ("impressions", "views", "reach", "likes", "replies", "comments", "reposts", "shares")) and summary["sample_size"] is None:
            continue  # An unpublished all-null draft is not an audience observation.
        hypothesis = payload.get("hypothesis") if isinstance(payload.get("hypothesis"), str) and payload["hypothesis"] in {"H1", "H2"} else None
        hook = payload.get("hook_type") if isinstance(payload.get("hook_type"), str) and payload["hook_type"] in HOOKS else None
        item = {"post_id": post_id, "account_key": account, "account_fingerprint": fingerprint,
                "platform": platform, "hypothesis": hypothesis, "hook": hook,
                "observed_at": observed.isoformat(), "denominator": denominator,
                "exposures": exposures, "engagements": engagements,
                "engagement_rate": round(rates, 6) if rates is not None else None,
                "eligible": exposures is not None and exposures >= minimum_exposures and rates is not None,
                "sentiment": summary}
        # Fingerprint only allowlisted aggregates; never hash or retain raw text.
        item["source_id"] = hashlib.sha256(json.dumps(item, sort_keys=True, allow_nan=False).encode()).hexdigest()
        posts.append(item)
        if item["eligible"]:
            grouped[(account, fingerprint, platform, denominator)].append(item)
    cohorts, lessons = [], []
    for (account, fingerprint, platform, denominator), rows in sorted(grouped.items()):
        ranked = sorted(rows, key=lambda row: (-row["engagement_rate"], row["post_id"]))
        enough = len(rows) >= minimum_posts
        cohorts.append({"account_key": account, "account_fingerprint": fingerprint, "platform": platform,
                        "denominator": denominator, "sample_posts": len(rows),
                        "status": "directional_only" if enough else "insufficient_posts",
                        "ranked_post_ids": [row["post_id"] for row in ranked] if enough else [],
                        "winner": None})
        strategies = defaultdict(list)
        for row in rows:
            if row["hypothesis"] and row["hook"] and type(row["exposures"]) is int and type(row["engagements"]) is int:
                strategies[(row["hypothesis"], row["hook"])].append(row)
        for (hypothesis, hook), evidence in sorted(strategies.items()):
            if len(evidence) < minimum_posts:
                continue
            counts = Counter(dict.fromkeys(_LABELS, 0))
            has_comments = False
            for row in evidence:
                if row["sentiment"]["counts"] is not None:
                    has_comments = True
                    counts.update(row["sentiment"]["counts"])
            lessons.append({"account_key": account, "account_fingerprint": fingerprint, "platform": platform,
                            "hypothesis": hypothesis, "hook": hook, "sample_posts": len(evidence),
                            "impressions": sum(row["exposures"] for row in evidence),
                            "engagements": sum(row["engagements"] for row in evidence),
                            "sentiment": dict(counts) if has_comments else None, "source_ids": sorted(row["source_id"] for row in evidence),
                            "confidence": "directional", "denominator": denominator})
    return {"version": VERSION, "status": "observed" if posts else "no_samples",
            "posts": posts, "cohorts": cohorts, "lessons": lessons[:10],
            "limitations": ["Latest cumulative snapshot only; missing is not zero; maximum age 48 hours.",
                            "Compare only one account identity, platform and exposure metric at a time.",
                            "Age, audience and selection effects remain; no causal or statistical winner claim.",
                            "English/Korean keyword sentiment is a partial sample; other languages and ambiguity remain unknown.",
                            "X reads mentions; Threads permission-limited replies cannot establish audience sentiment."]}
