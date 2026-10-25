# Upload-Post MCP 댓글·반응 조회와 Hermes 학습 범위

2026-10-25 17:33 KST 기준 실제 ChatGPT Upload-Post MCP 점검 결과입니다. **X 2개 계정의 멘션 조회와 연결된 X 3개·Threads 1개의 계정 분석 조회는 성공했습니다. 읽은 댓글은 0개이며, 현재 데이터로 감성 변화나 잘된 글을 학습했다고 주장할 수 없습니다.** 공식 X와 브랜드 Threads의 댓글 권한은 기존 글이 없어 미확인입니다.

Cloud 미생성 · 전체 예산 **$20** · **API 게시 OFF** · **LinkedIn 게시 차단** · 유료 모델 OFF를 유지했습니다. 게시·예약·댓글 작성·DM·좋아요·재연결·구독 구매 도구는 호출하지 않았습니다. 이 점검은 ChatGPT의 MCP 연결을 사용했으며 로컬 API 키를 MCP로 복사하지 않았습니다.

## 실제 계정별 결과

| 계정 / MCP 프로필 | 운영 상태·언어 | 댓글·답글 | 반응 지표와 해석 |
| --- | --- | --- | --- |
| X `@guidances_org` / `default` | 미선택·보류 / 영어 | 최근 목록에서 얻은 글 1개로 조회 성공. `source=mentions`, 0개 | 계정 팔로워 34·노출 9 반환. `available_metrics`는 팔로워·노출만 명시 |
| X `@cgcorporation` / `disanchor_twitter` | 미선택·보류 / 영어 | 최근 목록의 글 1개로 조회 성공. `source=mentions`, 0개 | 계정 팔로워 508·노출 182 반환. 좋아요·댓글 필드도 있지만 지원 목록 밖이므로 학습용 값에서 제외 |
| 공식 X `@disanchor_com` / `disanchor_x` | **운영 후보·미승인** / 영어 | 최근 글 0개, 댓글 호출하지 않음. 권한 미확인 | 계정 팔로워 1·노출 0 반환. 글별 성과를 확인한 결과가 아님 |
| Threads `@disanchor_com` / `default` | **운영 후보·미승인** / 영어 | 최근 글 0개, 답글 호출하지 않음. read-replies 미확인 | 팔로워·노출·좋아요·댓글·공유·인용·클릭 필드 반환, 모두 0. 분모 메타데이터가 달라 비교에 사용하지 않음 |
| Threads `@technodemocracystudio` / `disanchor_twitter` | **운영·재연결 보류 / 한국어** | 현재 연결 목록에 없음. 조회·재연결 시도 없음 | 현재 MCP 지표 미확인. 과거의 부분 조회 결과를 현재 권한으로 재사용하지 않음 |

연결된 네 X·Threads 항목은 모두 `reauth_required=false`였습니다. MCP 목록에는 native 계정 ID·OAuth scope가 없어 비공개 레지스트리의 native ID까지 다시 대조한 것은 아닙니다. 최근 글은 해당 프로필·플랫폼으로 제한한 `get_media` 응답에서 선택했습니다. MCP 성공 결과에는 원 서버 HTTP 상태가 노출되지 않아 “HTTP 200 확인”으로 바꾸어 기록하지 않았습니다.

분석 응답의 시계열 날짜는 X가 2026-09-26~10-25, Threads가 2026-09-25~10-25입니다. 위 숫자는 제공자의 계정 분석 반환값이며 7일 실험 성과나 글별 수치가 아닙니다. 2026-09-27~10-25 기간의 글별 캐시를 별도로 조회했으며, 이 기간과 계정 분석 기간을 같은 것으로 취급하지 않습니다.

## 실제로 읽힌 범위와 남은 권한

- **X:** 댓글 엔드포인트의 조회 성공만 확인했습니다. 공식 계약도 멘션 표본으로 설명하므로 특정 글의 모든 답글·답글 트리·대표 여론을 확보했다고 말할 수 없습니다. 반환 0개는 댓글이 전혀 없음, 부정 반응 또는 관심 부족의 증거가 아닙니다. 이번에는 실제 댓글 본문·작성자 표본을 확보하지 못했습니다.
- **Threads:** 공식 계약상 다른 사람 답글은 연결의 read-replies 권한에 달려 있습니다. 권한 부족 시 Upload-Post를 통해 작성한 답글만 `partial=true`로 반환할 수 있습니다. 현재 브랜드 계정은 유효한 기존 글을 얻지 못했으므로 이 분기를 직접 시험하지 못했습니다. 재연결·OAuth 동의·권한 상승은 하지 않았습니다.
- **기존 글이 없는 두 후보:** 앞으로 이미 게시한 본인 글의 URL/ID가 제공되면 그 글 1개·답글 1페이지로 추가 확인할 수 있습니다. 권한 확인을 위해 새 테스트 글을 게시하거나 남의 글·가짜 ID를 사용하지 않습니다. 개인 Threads는 사용자가 보류를 해제하기 전까지 그대로 둡니다.
- **반응 지표:** 응답에 숫자가 있어도 `available_metrics` 밖의 값은 `null`로 처리해야 합니다. 이번 X 분석에서는 좋아요·댓글·공유를 검증한 지원 지표로 인정하지 않았습니다. 브랜드 Threads는 `metric_type=views`인데 필드·라벨은 `impressions`이므로 노출 분모의 의미를 먼저 확인해야 합니다. 어느 쪽으로도 임의 변환하지 않습니다.

공식 근거: [Comments](https://docs.upload-post.com/api/comments) · [Analytics](https://docs.upload-post.com/api/get-analytics/) · [Media List](https://docs.upload-post.com/api/instagram-media). API 계약과 이번 계정의 실제 관측을 구분합니다.

## 글별 분석과 MCP 도구의 한계

선택된 `default`/Threads와 `disanchor_x`의 2026-09-27~10-25 캐시는 각각 0개였습니다. 캐시는 `snapshot_cache`이며, 새로 수집하거나 자동 갱신되는 데이터가 아닙니다. 유효한 제공자 `requestId`를 얻지 못해 `get_post_analytics`는 호출하지 않았습니다. 플랫폼 글 ID를 임의로 requestId 자리에 넣지 않습니다.

이번 MCP의 `get_cached_post_analytics.platform` 스키마에는 `x`가 없습니다. 공식 X 프로필에는 X만 연결되어 있으므로 플랫폼 인수를 생략해 해당 프로필의 캐시를 조회했습니다. 여러 플랫폼이 섞인 프로필에서 같은 방식을 그대로 쓰거나 지원하지 않는 enum을 보내지는 않았습니다. 이후 X 필터가 필요하면 커넥터 지원을 확인하거나 검증된 requestId별 조회를 사용해야 합니다.

최근 글 요청은 `limit=1`이었지만 X 두 계정에서 각각 5개가 반환됐습니다. 실제 댓글 조회에는 첫 글 하나씩만 사용했고 추가 페이지를 읽지 않았습니다. 따라서 요청 limit만 믿지 않고 호스트에서 처리량을 다시 제한해야 합니다.

## Hermes sentiment 학습에 사용할 수 있는 범위

| 자료 | 현재 가능한 사용 | 학습에 넣기 위한 조건 |
| --- | --- | --- |
| X 계정 팔로워·노출 | 수동 현황 참고 | 계정 단위 수치를 글별 관측으로 복제하지 않음. 이 자료만으로 게시물 순위·성과 skill을 만들지 않음 |
| X 멘션 표본 | 조건부 댓글 집계 | 계정·글 소유 확인, 중복·자기 답글 제외, 표본 범위 표시. 이번에는 0개여서 sentiment `unknown` |
| Threads 계정 지표 | 수동 현황 참고 | 분모 의미와 지원 지표 확인. 현재 빈 표본에서 인기 소재·긍정 감정 추론 금지 |
| Threads 다른 사람 답글 | **현재 미확인** | 유효한 본인 글과 read-replies 권한 검증, 명시적인 완전성 표시. `partial=true` 또는 누락·불명확은 `unknown` |
| 글별 노출·반응 | **이번 실제 조회 미검증** | 게시 영수증·native 계정 바인딩·제공자 requestId·지표 단위·관측 시각 검증 |
| 한국어 개인 Threads | **현재 사용 불가·보류** | 사용자 보류 해제와 별도 연결·권한 검증 전까지 조회·학습하지 않음 |

기존 워커는 비공개 API 키와 공식 REST 수집기를 사용하며, 로컬 게시 영수증으로 확인된 실험 글만 자동 수집합니다. **ChatGPT에 MCP를 연결했다고 Hermes Cloud가 그 연결을 상속하거나, 이번 MCP 관측값을 SQLite·skill에 자동 반입하는 것은 아닙니다.** 이번에 데이터 import, `collect`, `learn`, skill 작성·수정 또는 유료 모델 실행을 하지 않았습니다.

MCP 관측을 나중에 연결하려면 다음 반입 계약을 별도로 구현·검증해야 합니다.

1. 정확한 프로필·플랫폼·native 계정 ID·계정 지문과 본인 게시 영수증을 대조합니다. 과거 글을 실험 글로 자동 편입하지 않습니다.
2. MCP 응답을 닫힌 구조로 정규화하고 계정 지표와 글별 지표를 구분합니다. `available_metrics` 밖의 값은 `null`; `metric_type`·분모 충돌은 차단합니다. 기존 숫자 정규화 함수는 이 MCP 메타데이터까지 자동 해석하지 않습니다.
3. X의 `source=mentions`를 확인하고, Threads의 `partial` 누락·불명확도 차단합니다. 기존 REST 댓글 수집기는 `partial=true`를 거부하지만, 누락된 Threads 완전성 표시와 X source를 명시적으로 검증하는 조건은 추가 보강이 필요합니다. 현재 MCP 자동 반입 경로는 없습니다.
4. 원문은 메모리에서만 다루고 개인정보·링크·지시문·중복·자기 답글을 제외합니다. 모델·Telegram·Git·skill에는 댓글 원문·작성자 ID를 넣지 않고 검증된 집계만 전달합니다. 이번 문서와 근거 파일에도 원문·게시물 ID·native ID·토큰을 넣지 않았습니다.
5. 기존 학습 기준을 유지합니다. **동일 계정·지문·플랫폼·가설·hook·분모의 글 3개 이상, 각 노출 100회 이상**이어야 성과 교훈을 만들고, sentiment 방향에는 **분류 가능한 댓글 5개 이상·분류율 50% 이상**이 필요합니다. 48시간 넘은 관측은 학습에서 제외합니다. 영어·한국어 키워드 분류의 반어·모호성 한계는 유지합니다.
6. 검증된 집계만 버전·hash·출처와 함께 skill로 저장하고 다음 리서치·기획에 반영합니다. 긍정이면 명확성을 유지하고, 부정이면 단정·과장을 줄이며, 혼합이면 장단점을 인정합니다. 표본 부족은 중립을 유지합니다. 좋아요·무반응을 감정으로 바꾸지 않으며 게시 빈도·광고성·권한을 늘리지 않습니다.

**현재 실제로 학습 가능한 sentiment 표본과 검증된 새 성과 교훈은 없습니다.** 연결 가능성을 확인한 점검 결과이며 “반응을 읽어 이미 스스로 최적화했다”는 결과가 아닙니다. 기존 [피드백 기준](marketing-feedback.md)·[Hermes auto-skill 계약](hermes-auto-skills.md)을 그대로 적용합니다.

## 호출·보안·운영 기록

[정제한 MCP 점검 근거](evidence/upload-post-mcp-feedback-2026-10-25.json)에 상태·지원 지표·집계만 저장했습니다. 읽기 도구 호출은 13회(인증 1, 프로필 1, 최근 글 4, 계정 분석 3, 댓글 2, 캐시 2)입니다. MCP 내부의 플랫폼 API 호출 수와 HTTP 상태는 공개되지 않아 별도로 단정하지 않습니다. 페이지 추가 탐색·동일 오류 재시도·스크래핑·새 webhook은 없습니다.

추가 구독·Cloud·유료 모델은 시작하지 않았습니다. API 사용량은 기존 제공자 요금제에 반영될 수 있으므로 외부 서비스 청구액을 0으로 확정하지 않습니다. 전체 $20, 모델 $5·도구 $2 내부 상한, $14 경고·$18 신규 선택 작업 중단, 게시 OFF·LinkedIn 차단을 유지합니다. 경고·권한 오류·429는 중단·운영자 검토 사유이며 다른 계정으로 우회하지 않습니다.

## English summary

Read-only ChatGPT MCP checks succeeded for profile analytics on three X connections and one Threads connection. Two X comment calls returned empty mentions samples. The selected official X and brand Threads had no listed posts, so their comment permissions remain unverified. Korean Technodemocracy Threads is disconnected and stays on hold. Account analytics are not post-level learning evidence; X availability metadata and conflicting Threads denominator metadata require normalization. No MCP-to-Hermes import, skill update, Cloud instance, social post or paid model run was started. USD 20, publishing OFF and the LinkedIn block remain unchanged.

[README](../README.ko.md) · [과거 REST 권한 점검](comment-permissions.ko.md) · [배포 체크리스트](deployment-checklist.ko.md)
