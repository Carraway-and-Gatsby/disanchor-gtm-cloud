#!/usr/bin/env python3
"""Build offline HTML review surfaces from public source and example accounts."""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("cloud_preflight", ROOT / "scripts/cloud-preflight.py")
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)

STYLE = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5f1;color:#20372b;font:16px/1.65 system-ui,sans-serif}main{max-width:1050px;margin:48px auto;padding:0 24px 56px}h1{font-size:clamp(28px,5vw,42px);line-height:1.25;letter-spacing:-.04em}h2{margin-top:40px;font-size:22px}p{max-width:80ch}a{color:#1a6440;text-underline-offset:3px;overflow-wrap:anywhere}article,section.card{background:#fff;border:1px solid #d4ded6;border-radius:14px;padding:24px;margin:18px 0}aside{background:#fff0c7;border:1px solid #e3d49b;border-radius:12px;padding:18px 20px}small,.muted{color:#557460}.copy{white-space:pre-wrap;font-size:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}.grid article{margin:0}code{font:13px/1.5 ui-monospace,monospace;overflow-wrap:anywhere}li{margin:8px 0}.badge{display:inline-block;background:#e4eee4;border-radius:100px;padding:3px 11px;font-size:13px}.file-list{columns:2;column-gap:32px}.file-list li{break-inside:avoid}footer{border-top:1px solid #d4ded6;margin-top:40px;padding-top:18px;font-size:14px}@media(max-width:650px){main{margin:28px auto;padding:0 16px 32px}.file-list{columns:1}article,section.card{padding:20px}}
"""


def document(title, body):
    return ("<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            f"<title>{escape(title)}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>\n")


def link(path, label):
    return f'<a href="{escape(Path(path).absolute().as_uri(), quote=True)}">{escape(label)}</a>'


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".review-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def preview_html(config, registry, output_dir):
    from gtm.config import slots, timestamp
    from gtm.policy import POLICY_VERSION, seed_text, validate_content

    selected = {"x": config.accounts.x_account, "threads": config.accounts.threads_account}
    rows = [seed_text(slot, config) for slot in slots(config, timestamp("2026-10-24T12:00:00Z"))]
    previous = []
    for row in rows:
        validate_content(row["platform"], row["text"], previous)
        previous.append(row["text"])
    body = ("<span class=\"badge\">DRAFT · 미게시 검토 후보</span><h1>초기 7일: 공감과 대화 중심</h1>"
            "<aside>Disanchor가 아직 개발 중이라는 맥락을 각 글에 자연스럽게 담았습니다. "
            "링크·가입·프로필 방문·구매 유도 없이 유용한 관찰과 질문에 집중합니다. "
            "게시 승인은 별도로 필요하며 계정 안전이나 바이럴 성과를 보장하지 않습니다.</aside>"
            f"<p>정책 <code>{escape(POLICY_VERSION)}</code> · 총 {len(rows)}개 후보 · "
            "X/Threads 각각 최대 5개. 날짜는 시작 전 검토용 Day 번호입니다.</p>"
            f"<p>{link(output_dir / 'review-index.html', '전체 검토 파일')} · "
            f"{link(ROOT / 'docs/posting-policy.md', '게시 정책')} · "
            f"{link(ROOT / 'prompts/content-system.md', '콘텐츠 작성 프롬프트')}</p>")
    for row in rows:
        body += (f"<article><small>Day {row['day']} · {escape(row['platform'])} · "
                 f"{escape(selected[row['platform']])} · {escape(row['id'])} · CTA 없음</small>"
                 f"<p class=\"copy\">{escape(row['text'])}</p></article>")
    body += ("<h2>7개 계정별 접근</h2><p>이 목록은 전략 예시입니다. 현재 캠페인 선택은 "
             f"<code>{escape(selected['x'])}</code>와 <code>{escape(selected['threads'])}</code>이며, "
             "계정 수가 총 게시 한도나 예산을 늘리지 않습니다. Instagram·LinkedIn은 전략과 초안 준비만 지원합니다.</p><div class=\"grid\">")
    for row in registry.all():
        context = registry.context(row["id"])
        body += (f"<article><small>{escape(row['upload_post_profile'])} / {escape(row['platform'])}</small>"
                 f"<h3>{escape(row['id'])}</h3><p>{escape(context['approach'])}</p>"
                 f"<p class=\"muted\">{escape(context['voice'])}</p>"
                 f"<p>{link(ROOT / row['prompt_file'], '계정 프롬프트')}</p></article>")
    body += ("</div><footer>체크인된 예시 레지스트리와 검증한 seed 문구로 만든 오프라인 미리보기입니다. "
             "runtime.env, 실제 계정 바인딩, 운영 DB를 읽거나 외부 서비스를 호출하지 않습니다.</footer>")
    return document("Disanchor 초기 게시 후보", body), len(rows)


def bundle_state(entries):
    archive_path = ROOT / "reports/disanchor-cloud.zip"
    if not archive_path.is_file() or archive_path.is_symlink():
        return "아직 생성되지 않았습니다. 소스가 확정되면 패키징합니다."
    try:
        with zipfile.ZipFile(archive_path) as archive:
            info = archive.getinfo("bundle-manifest.json")
            if info.file_size > 256 * 1024:
                return "manifest 확인이 필요합니다."
            manifest = json.loads(archive.read(info))
        expected = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for name, data in sorted(entries.items())]
        if manifest.get("files") != expected:
            return "현재 소스와 달라 재생성이 필요합니다. 기존 ZIP은 최종 배포본이 아닙니다."
        return "현재 허용 목록의 소스와 manifest가 일치합니다. 실제 Cloud 배포는 별도입니다."
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return "manifest 확인이 필요합니다."


def review_html(entries, output_dir):
    bundle = ROOT / "reports/disanchor-cloud.zip"
    body = ("<span class=\"badge\">DEPLOYMENT PREPARATION</span><h1>Disanchor 검토 파일</h1>"
            "<aside>Cloud 배포 준비 자료입니다. 실제 인스턴스 배포·유료 추론·게시·Telegram 전송은 "
            "완료 상태가 아닙니다. 연결 점검 기록이 있더라도 조회 결과와 게시 승인은 구분합니다.</aside>"
            f"<p>{link(output_dir / 'content-preview.html', '현재 게시 후보 10개')} · "
            f"{link(ROOT / 'docs/cloud-deployment.md', 'Cloud 배포 준비 안내')} · "
            f"{link(bundle, 'Cloud 소스 ZIP 직접 열기')}</p>"
            f"<p class=\"muted\">ZIP 상태: {escape(bundle_state(entries))}</p>"
            "<h2>검증과 시뮬레이션</h2><p>테스트 범위와 실제 실행 결과는 검증 기록에서 확인합니다. "
            "데모 지표·게시·비용은 시뮬레이션이며 실제 성과를 뜻하지 않습니다.</p><ul>")
    reports = [
        ("reports/verification.txt", "검증 실행 기록"),
        ("reports/upload-post-connection.json", "Upload-Post 정규화 연결 조회 결과"),
        ("reports/upload-post-feedback-check.json", "Upload-Post 자체 게시물 조회 점검"),
        ("reports/native-skill-verification.json", "설치된 Hermes native skill 생성·수정·복구 검증"),
        ("reports/learning-independent-review.json", "학습 보호 장치 독립 검토"),
        ("reports/readiness.json", "최종 준비 상태와 GitHub Actions 결과"),
        ("reports/demo/index.html", "시뮬레이션 대시보드"),
        ("reports/demo/final_report.md", "시뮬레이션 최종 보고서"),
        ("reports/demo/daily_metrics.csv", "시뮬레이션 일별 지표"),
        ("reports/demo/post_metrics.csv", "시뮬레이션 게시물 지표"),
        ("reports/demo/cost_ledger.csv", "시뮬레이션 비용 원장"),
        ("reports/demo/experiment_decisions.json", "시뮬레이션 의사결정"),
        ("reports/demo/routing_runs.json", "시뮬레이션 4단계 라우팅"),
        ("reports/demo/telegram-preview.txt", "미전송 Telegram 요약 예시"),
        ("reports/demo/marketing-feedback.json", "시뮬레이션 마케팅 집계"),
        ("reports/demo/learning-status.json", "시뮬레이션 학습 상태"),
    ]
    for relative, label in reports:
        path = ROOT / relative
        if path.is_file() and not path.is_symlink():
            body += f"<li>{link(path, label)}</li>"
    body += "</ul>"
    groups = [
        ("설정과 안내", lambda name: "/" not in name or name.startswith("docs/")),
        ("작성·계정 프롬프트", lambda name: name.startswith("prompts/")),
        ("실행 코드", lambda name: name.startswith("gtm/")),
        ("테스트", lambda name: name.startswith("tests/")),
        ("배포·검증·CI", lambda name: name.startswith(("scripts/", ".github/"))),
        ("선택 웹사이트 연결 코드", lambda name: name.startswith("web/")),
    ]
    linked = set()
    for title, predicate in groups:
        names = sorted(name for name in entries if predicate(name))
        linked.update(names)
        body += f"<h2>{title}</h2><ul class=\"file-list\">"
        body += "".join(f"<li>{link(ROOT / name, name)}</li>" for name in names)
        body += "</ul>"
    if linked != set(entries):
        raise ValueError("review_index_source_coverage_incomplete")
    body += (f"<footer>소스 파일 {len(entries)}개의 직접 링크입니다. 파일을 목록에 넣었다는 것은 "
             "사람의 최종 승인을 뜻하지 않습니다. 비밀 파일·로컬 계정 바인딩·운영 DB는 이 목록과 "
             "Cloud ZIP에 포함하지 않습니다.</footer>")
    return document("Disanchor 검토 파일", body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)
    output_dir = args.output_dir.absolute()
    # Read only the source allowlist and the checked-in example account data.
    # Never follow operational configuration to accounts.local.toml or state.
    entries = checks.source_entries(ROOT)
    raw = tomllib.loads(entries["config.toml"].decode())
    if raw.get("accounts", {}).get("registry_file") != "accounts.example.toml":
        raise ValueError("review_requires_example_registry_config")
    from gtm.accounts import AccountRegistry
    from gtm.config import Config
    config = Config.load(ROOT / "config.toml")
    registry = AccountRegistry(ROOT / "accounts.example.toml")
    preview, count = preview_html(config, registry, output_dir)
    index = review_html(entries, output_dir)
    atomic_write(output_dir / "content-preview.html", preview)
    atomic_write(output_dir / "review-index.html", index)
    print(json.dumps({"status": "review_surfaces_created", "candidate_posts": count,
                      "source_links": len(entries), "account_strategies": len(registry.all()),
                      "outputs": [str(output_dir / name) for name in ("content-preview.html", "review-index.html")],
                      "credentials_read": False, "http_calls": 0, "cloud_deployed": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
