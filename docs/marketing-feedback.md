# Marketing feedback / 마케팅 반응 학습

Latest live MCP evidence / 최신 실제 MCP 근거: [2026-10-25 댓글·반응 조회와 학습 가능 범위](upload-post-mcp-feedback.ko.md). Two X reads returned empty mentions samples; selected-account reply access remains unverified. Profile analytics are not post-level training observations. No automatic MCP import exists; metadata normalization and completeness checks are required before adding one. / X 2개는 멘션 0개, 운영 후보의 답글 권한은 미확인입니다. MCP 계정 지표를 실험 글별 수치로 가져오지 않으며 현재 sentiment 교훈을 추가하지 않았습니다.

The worker observes performance and public responses to its own confirmed posts, then feeds bounded aggregates back into research, planning and reusable learning. It never replies to a commenter, sends a DM, likes a comment, or changes publishing approval. / 실행기는 자신이 게시한 것으로 확인된 글의 성과와 공개 반응을 집계하여 리서치·기획·재사용 학습에 반영합니다. 댓글 답변, DM, 좋아요, 게시 승인 변경은 하지 않습니다.

## What is measured / 측정 항목

| Signal / 신호 | Method / 방식 | Interpretation / 해석 |
| --- | --- | --- |
| Post performance / 글 성과 | Latest per-post cumulative metrics, never a sum of repeated snapshots / 게시물별 최신 누적 관측치만 사용 | Compare only the same account identity, platform and exposure denominator / 동일 계정·플랫폼·노출 지표 안에서 비교 |
| Engagement rate / 반응 비율 | `(likes + replies/comments + reposts/shares) / impressions, views or reach` | All three numerator components must exist; missing data stays `null` / 세 항목이 모두 있을 때만 계산, 누락은 `null` |
| Comment sentiment / 댓글 감성 | Local English/Korean keyword classification: positive, negative, mixed, unknown / 로컬 영어·한국어 키워드 분류 | Weak sample signal, not a representative survey or reliable interpretation of sarcasm / 약한 표본 신호이며 대표 여론·반어 해석이 아님 |
| Reusable lessons / 재사용 학습 | At least three posts of the same account, platform, denominator, hypothesis and hook, each with at least 100 exposures / 같은 조건의 글 3개 이상, 글마다 노출 100 이상 | Directional observations; never a causal or statistically proven winner / 방향성 관측이며 인과관계·통계적 승리로 단정하지 않음 |

A single successful post can appear in the report, but cannot establish a winning strategy or an evidence-backed skill. Publication age, audience selection and algorithmic distribution still affect comparison. Missing comments remain unknown, even when likes are high. / 글 하나가 잘되어도 성공 전략이나 근거 기반 스킬을 확정하지 않습니다. 게시 후 경과 시간·독자 구성·알고리즘 노출의 영향이 남습니다. 좋아요가 많아도 댓글 자료가 없으면 감성은 알 수 없습니다.

The fixed seven-day experiment still ends after 168 hours and still allows only ten publishing slots. Collection runs within the existing interval and stops with the experiment; this feature does not silently extend the campaign. / 기존 168시간 실험·게시 슬롯 10개의 제한을 유지합니다. 수집도 기존 주기와 실험 종료 조건을 따르며 캠페인을 자동 연장하지 않습니다.

## API support and limitations / API 지원과 한계

Official API contract checked on 2026-10-24 / 공식 API 계약 확인일: 2026-10-24.

- [Upload-Post Comments](https://docs.upload-post.com/api/comments): the client uses only `GET /api/uploadposts/comments`, with `platform`, `user`, `post_id`, `limit` and the returned `after` cursor. X results are a **mentions sample**. Threads may expose only replies created through Upload-Post until read-replies permission is granted; a `partial: true` response produces unknown audience sentiment. / X는 멘션 표본입니다. Threads 읽기 권한이 부족하면 부분 응답을 여론으로 해석하지 않습니다.
- [Upload-Post analytics](https://docs.upload-post.com/api/get-analytics/): existing post analytics requests retrieve live per-post metrics. Cached analytics are not a background refresh service. This implementation keeps the live collection bounded to this experiment's posts. / 캐시 조회가 자동 최신화는 아닙니다. 현재 구현은 실험 게시물만 제한적으로 조회합니다.
- [User profiles](https://docs.upload-post.com/api/user-profiles/): a fresh profile lookup verifies the exact native account and explicit non-expired connection before comments are read. Reconnection, missing identity or required authentication blocks that read. / 댓글 조회 전 실제 계정 ID와 인증 상태를 확인하며, 재연결로 계정이 달라지면 조회를 차단합니다.

Provider permission, rate or availability errors produce a safe status code and `null` sentiment. There is no same-run retry. No API call creates an AutoDM monitor or requests additional user data. / 권한·한도·서버 오류는 안전한 상태 코드와 `null`로 기록하며 같은 실행에서 재시도하지 않습니다. AutoDM이나 추가 개인정보 수집을 생성하지 않습니다.

A confirmed comment-API rate limit also pauses an active campaign and revokes posting authorization through the existing incident handler. It does not extend or restart an ended experiment. / 댓글 API의 명확한 한도 오류도 진행 중 캠페인을 일시 중지하고 기존 게시 승인을 해제합니다. 종료된 실험은 다시 시작하지 않습니다.

## Privacy and bounds / 개인정보와 한도

- The default is at most ten owned published posts per collection, forty comment records per post and two pages; immutable code limits are ten posts, fifty records and two pages. Identity checks add at most one GET per connected profile/platform during that collection. / 기본 수집량은 게시물 10개, 글당 댓글 레코드 40개·2페이지입니다. 코드 상한은 10개·50개·2페이지이며 계정별 확인 GET이 추가됩니다.
- Raw comment text, names, handles, comment IDs, profile URLs and author IDs are never stored or sent to the model, Telegram, reports or skills. Text is examined only in memory, with contacts, URLs and handles removed before classification. / 댓글 원문·이름·핸들·댓글 ID·작성자 ID는 저장하거나 모델·Telegram·리포트·스킬로 보내지 않습니다.
- The brand's own replies, unknown ownership, duplicate comment IDs and identical normalized text are excluded. The resulting count is a deduplicated text sample, not a unique-person count. / 계정 자신의 댓글, 소유자 확인이 안 된 댓글, 중복 ID·중복 문구를 제외하며 고유 사용자 수라고 주장하지 않습니다.
- Negation, obvious instruction text, unsupported language and unclear wording stay `unknown`. At least five classified records and 50% classification coverage are needed before displaying a directional sentiment label. / 부정·지시문·미지원 언어·불명확한 표현은 `unknown`입니다. 분류 가능한 표본 5개와 분류율 50%를 충족해야 감성 방향을 표시합니다.
- Old observations expire from learning after 48 hours. Account registry fingerprints partition all aggregates, so reconnecting or changing an account strategy cannot mix identities. / 48시간보다 오래된 관측은 학습에서 제외하고 계정 레지스트리 지문으로 구분합니다.

## Integration contract / 연결 계약

`UploadPostFeedbackClient(api_key, allowed_posts, transport=None, max_pages=2, max_comments=40, min_comments=5)` accepts only a bounded allowlist assembled from confirmed publication receipts. Each entry has exactly:

```text
post_id, account_key, account_fingerprint, profile_username,
platform, platform_post_id, native_account_id
```

`collect(local_post_id)` returns status, coverage, bounded counters and `sentiment` aggregates. It cannot accept an arbitrary remote post ID. / 임의의 외부 게시물 ID는 받지 않습니다.

`aggregate_feedback(metric_records, sentiment_records, now=..., minimum_posts=3, minimum_exposures=100, min_comments=5)` consumes `Store.records()` output and emits a closed `version/status/posts/cohorts/lessons/limitations` envelope. Metrics snapshots also carry `account_key`, `account_fingerprint`, `hypothesis` and `hook_type`. / 성과·감성 스냅샷에서 닫힌 구조의 집계 결과만 만듭니다.

The lesson field `impressions` is an exposure total whose exact meaning is provided by `denominator`; it must be labelled views when the denominator is views. No comment observation yields `sentiment: null`; an observed empty sample can yield zero counts. Fractional metric counts remain visible but cannot create a lesson. / `impressions` 필드의 실제 노출 단위는 `denominator`로 구분합니다. 댓글 미관측은 `null`, 실제 빈 응답은 0으로 구분하며 소수 지표를 임의 반올림해 학습하지 않습니다.

Validation uses mocked official contracts, not fabricated live results. Run `python3.12 -m unittest tests.test_feedback -v`. / 검증은 공식 계약 기반 모의 응답을 사용하며 실제 결과로 표시하지 않습니다.
