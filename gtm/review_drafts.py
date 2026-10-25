"""Owner review of a pinned public document; never reads the publishing queue."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .policy import validate_content

DOCUMENT_SHA256 = "bee175c828b6df98298878a349d7ef745e0a37c0d4988907079c1778852619a3"
# Fixed public identities and owner decisions, not live connection assertions.
ACCOUNTS = {
    "disanchor_x": ("X @disanchor_com", "운영 후보 · 미승인", "en", "x"),
    "default_threads": ("Threads @disanchor_com", "운영 후보 · 미승인", "en", "threads"),
    "default_x": ("X @guidances_org", "미선택 · 보류", "en", "x"),
    "personal_x": ("X @cgcorporation", "미선택 · 보류", "en", "x"),
    "personal_threads": ("Threads @technodemocracystudio", "운영·재연결 보류", "ko", "threads"),
    "personal_linkedin": ("LinkedIn (게시 주체 확인 필요)", "게시 차단", "en", "linkedin"),
    "default_instagram": ("Instagram @disanchor_com", "운영 제외 · 참고용", "en", "instagram"),
}


def draft_review(command, *, root=None):
    """Exact command allowlist. No user-selected path, DB query or execution."""
    if command == "/drafts":
        lines = ["Disanchor 계정별 검토 초안", "검토 문서 기준 · API 게시 OFF · 예산 $20 · LinkedIn 게시 차단"]
        for key, (label, status, language, _) in ACCOUNTS.items():
            lines.append(f"\n{label} · {status} · {'한국어' if language == 'ko' else '영어'}\n/drafts {key}")
        return "\n".join(lines) + "\n\n조회만 가능하며 게시·예약·승인이 아닙니다."
    commands = {"/drafts " + key: key for key in ACCOUNTS}
    if command not in commands:
        return None
    key = commands[command]
    label, status, language, platform = ACCOUNTS[key]
    path = Path(root or Path(__file__).resolve().parents[1]) / "docs/account-drafts.en-ko.md"
    try:
        if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 20000:
            raise ValueError("unreviewed_source")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != DOCUMENT_SHA256:
            raise ValueError("unreviewed_source")
        section = raw.decode().split(f'<a id="account-{key}"></a>', 1)[1].split('<a id="account-', 1)[0]
        drafts = []
        for chunk in re.split(r"\n### [A-Z]+-\d{2}\n", section)[1:]:
            quotes = re.findall(r"^> (.+)$", chunk, flags=re.M)
            text = quotes[1 if language == "ko" else 0]
            if platform in {"x", "threads"}:
                validate_content(platform, text, drafts, language=language)
            drafts.append(text)
        if len(drafts) != 2:
            raise ValueError("invalid_review_document")
        result = (f"Disanchor 검토 초안\n{label}\n{status} · {'한국어' if language == 'ko' else '영어'}\n\n"
                  + "\n\n".join(f"{i}. {text}" for i, text in enumerate(drafts, 1))
                  + "\n\n검토 문서의 초안입니다. 게시 대기열과 별개이며 승인·예약·게시하지 않습니다.\nAPI 게시 OFF · 예산 $20 · LinkedIn 게시 차단")
        if len(result) > 4096:
            raise ValueError("review_too_large")
        return result
    except (OSError, ValueError, IndexError, UnicodeError):
        return "초안 문서의 버전·내용 검증이 필요합니다. 전송하지 않았습니다. API 게시 OFF · 예산 $20 유지."
