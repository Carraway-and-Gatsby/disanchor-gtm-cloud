from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import urlencode, urlsplit, parse_qs

POLICY_VERSION = "2026-10-25.2"
ALLOWED_ACTIONS = {"draft", "schedule_own_text", "read_own_metrics", "cancel_own_schedule", "report"}
# Recognize current development in ordinary prose. This is a bounded language
# check, not a requirement to attach a fixed sentence or a claim-verification AI.
# Keep older direct disclosures recognizable when reviewing persisted drafts.
DEVELOPMENT_DISCLOSURE = re.compile(r"""
    \b(?:
        Disanchor\s+(?:is|remains)\s+(?:still\s+)?
            (?:in\s+(?:active\s+)?development|being\s+(?:developed|built))
        |Disanchor\s*,\s*(?:still\s+)?in\s+(?:active\s+)?development
        |Disanchor\s+while\s+it(?:['’]s|\s+is)\s+(?:still\s+)?in\s+development
        |Disanchor['’]s\s+(?:ongoing|current|active)\s+development
        |Disanchor\s+(?:isn['’]t|is\s+not)\s+(?:yet\s+)?(?:released|launched)(?:\s+yet)?
        |Disanchor\s+(?:hasn['’]t|has\s+not)\s+been\s+(?:released|launched)(?:\s+yet)?
        |(?:the\s+)?not[-\s]yet[-\s]released\s+Disanchor
        |(?:while\s+|as\s+)?(?:we(?:['’]re|\s+are)|i(?:['’]m|\s+am))\s+
            (?:still\s+|currently\s+)?(?:building|developing)\s+Disanchor
            (?!['’]s\b)(?=\s*[,.;:!?—-]|\s+(?:and|but|because|so|while|before)\b|$)
        |(?:as|while)\s+we\s+(?:build|develop)\s+Disanchor
            (?:\s+(?:before\s+(?:its\s+first\s+)?release|ahead\s+of\s+release))?
            (?!['’]s\b)(?=\s*[,.;:!?—-]|\s+(?:and|but|because|so|while)\b|$)
    )\b
    |(?:개발\s*중인\s+Disanchor(?=[^A-Za-z0-9_]|$)
       |Disanchor(?:는|가)?\s*(?:아직\s+)?개발\s*중(?:입니다|이에요|이며|이고|이라서|이므로|인)(?=[\s,.;:!?]|$)
       |Disanchor를\s+(?:개발하면서|만들면서|(?:개발하는|만드는)\s+중(?:입니다|이며)))
""", re.I | re.X)
LINK_PATTERN = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,63}\b)", re.I)
PROMOTIONAL_CTA = re.compile(r"\b(?:sign\s?up|join\s+(?:(?:our|the)\s+)?waitlist|subscribe|buy\s+now|book\s+(?:a\s+)?demo|"
                             r"link\s+in\s+(?:our\s+|my\s+)?bio|check\s+(?:our|the|my)\s+(?:profile|bio)|"
                             r"visit\s+(?:our|the|my)\s+(?:site|website|profile)|try\s+(?:disanchor|our\s+product)|"
                             r"get\s+(?:early\s+)?access|comment\s+.+?\s+(?:for|to\s+get)\s+access)\b", re.I)
KOREAN_PROMOTION = re.compile(r"(?:지금\s*가입|가입(?:하세요|하기|해\s*주세요)|회원\s*가입|대기\s*명단|사전\s*신청|"
                              r"구매(?:하세요|하기)|무료\s*체험|데모\s*신청|팔로우(?:하세요|해|하기)|"
                              r"좋아요(?:를)?\s*눌러|공유(?:해|해\s*주세요|하기)|프로필.{0,15}링크|링크.{0,15}프로필|"
                              r"출시\s*완료|이미\s*출시|정식\s*출시됐|성능\s*보장|100%\s*보장)")


def content_hash(text):
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()


def comparison_body(text):
    """Compare the substantive copy even when disclosure phrasing or placement varies."""
    body = DEVELOPMENT_DISCLOSURE.sub(" ", text).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", body).split())


def require_action(action):
    if action not in ALLOWED_ACTIONS:
        raise ValueError("Outgoing action denied by experiment policy")


def validate_content(platform, text, previous=(), *, language=None):
    if platform not in {"x", "threads"} or not isinstance(text, str) or not text.strip():
        raise ValueError("Invalid platform or empty text")
    # Conservative weighted length avoids relying on Python character counts for X CJK/emoji.
    length = sum(1 if ord(c) < 128 else 2 for c in text) if platform == "x" else len(text.encode("utf-16-le")) // 2
    if length > (280 if platform == "x" else 500):
        raise ValueError("Text exceeds platform limit")
    if re.search(r"[\x00-\x08\x0b-\x1f]|@[\w.]|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text):
        raise ValueError("Mentions, contact details, or control characters are forbidden")
    if re.search(r"(?i)(api[_ -]?key|bearer\s|sk-[a-z0-9]|password\s*[:=]|\b(?:\+?\d[\d -]{8,}\d)\b)", text):
        raise ValueError("Possible confidential information")
    if text.count("#") > 1:
        raise ValueError("Hashtag stuffing is forbidden")
    if re.search(r"(?i)\b(follow me|like and|retweet|repost to|tag your|giveaway|guaranteed|zero latency|available now|sign up now|buy now|unlimited gpu)\b", text):
        raise ValueError("Engagement bait or unverified promise")
    if LINK_PATTERN.search(text) or PROMOTIONAL_CTA.search(text) or KOREAN_PROMOTION.search(text):
        raise ValueError("Initial seven-day conversation-only campaign forbids links and promotional CTAs")
    if not DEVELOPMENT_DISCLOSURE.search(text):
        raise ValueError("Every post must explicitly state that Disanchor is still in development")
    if language == "ko":
        body = re.sub(r"Disanchor", "", text, flags=re.I)
        hangul = len(re.findall(r"[가-힣]", body))
        # Conservative script check, not a semantic language detector. A short
        # Korean disclosure must not disguise a mainly English post.
        if hangul < 20 or hangul < len(re.findall(r"[A-Za-z]", body)):
            raise ValueError("Korean account requires a Korean body")
    # Development can be mentioned anywhere in a sentence. Moving or rephrasing
    # that context must not disguise copied content or inflate similarity.
    normalized = comparison_body(text)
    if len(normalized) < 30:
        raise ValueError("Post needs a substantive observation or workflow question beyond development status")
    for old in previous:
        prior_body = comparison_body(old)
        if SequenceMatcher(None, normalized, prior_body).ratio() >= .78:
            raise ValueError("Duplicate or substantially similar content")
    return True


def tracked_link(base_url, platform, content_id):
    return base_url.rstrip("/") + "/?" + urlencode({"utm_source": platform, "utm_medium": "organic_social",
          "utm_campaign": "disanchor_gtm_7d", "utm_content": "x_profile" if platform == "x" else content_id})


def valid_profile_link(url, campaign):
    p = urlsplit(url)
    q = parse_qs(p.query)
    return (p.scheme == "https" and p.netloc == "disanchor.com" and
            all(q.get(k) == [v] for k, v in {"utm_source": "x", "utm_medium": "organic_social",
                "utm_campaign": campaign, "utm_content": "x_profile"}.items()))


# Reviewable seed library. Only the next day's slots are materialized by the worker.
# Any model-edited text loses approval and requires an owner review.
SEEDS = {
 "x_01": ("H1", "question", "none", "We're still building Disanchor, and here's a useful scope question: which Windows app gives an otherwise retired laptop its second job? A machine can stop being your daily driver without becoming replaceable."),
 "threads_01": ("H2", "situation", "none", "As we develop Disanchor ahead of release, the new-computer ritual is a useful place to start: files copied, apps installed, one tiny setting forgotten. What would you put on a setup checklist that an installer can't know?"),
 "x_02": ("H2", "observation", "none", "'Works on my machine' can mean the documentation lives in local settings. For Disanchor, still in development, a useful design question is: what should someone need beyond a README to rebuild a workspace?"),
 "threads_02": ("H1", "question", "none", "Disanchor isn't released yet, so here's a problem worth unpacking: packing one light laptop, then remembering the old Windows-only tool needed for a 10-minute task. What do you bring along when one small job dictates the whole bag?"),
 "x_03": ("H1", "tradeoff", "none", "For Disanchor's ongoing development, speed and compatibility are separate design questions. More RAM won't solve an operating-system mismatch. Before upgrading, is the task slow, or can it not run at all?"),
 "threads_03": ("H2", "situation", "none", "We're developing Disanchor, and a keyboard shortcut is a good example of a small dependency: on a different machine, the action your hands expect may disappear. Which part of a familiar setup is hardest to explain to someone else?"),
 "x_04": ("H2", "question", "none", "A backup saves files; a rebuild plan explains how to use them again. As we build Disanchor before its first release, which parts of a familiar setup should a recovery checklist include?"),
 "threads_04": ("H1", "situation", "none", "One question for Disanchor while it's still in development: what earns an old computer a permanent spot in your drawer? A single client project, a peripheral or an irreplaceable app can matter more than the spec sheet."),
 "x_05": ("H1", "question", "none", "Disanchor is still being built around questions like this: when does opening the client's file matter more than finishing the task faster? Compatibility can be a prerequisite; speed is an optimization."),
 "threads_05": ("H2", "reflection", "none", "Writing setup instructions for a stranger is a useful thought experiment while Disanchor is being developed. What would the instructions miss: fonts, folders, shortcuts, licences, or the order things need to be installed?"),
}


def seed_text(slot, config):
    hypothesis, hook, cta, text = SEEDS[slot["id"]]
    if cta != "none":
        raise ValueError("Initial campaign must remain conversation-only")
    return {**slot, "text": text, "hypothesis": hypothesis, "hook_type": hook, "cta_type": cta,
            "audience": "demonstrated Windows/workflow interests", "variant": "organic-v3"}
