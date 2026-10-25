# MCP 반응 → Hermes sentiment 자동 반입

공식 Upload-Post MCP에서 본인 글의 반응을 읽고 검증·정규화·비공개 저장·skill 갱신까지 이어지는 수집 경로를 구현했습니다. **Cloud 미생성, 전체 $20, API 게시 OFF, LinkedIn 게시 차단, 유료 모델 OFF**는 유지합니다. 새 연결·게시·예약·cron은 생성하지 않았습니다.

## 실제 동작 경로

```text
기존 collect 작업 / collect-mcp
  → 미시작·종료·사고·청구 미확인·예산 한도 검사
  → 현재 선택 계정의 검증된 본인 게시 영수증
  → 반입 claim과 이전 관측 무효화 기록을 먼저 commit
  → 공식 REST 읽기로 native 계정 ID·재인증 상태 재검증
  → 공식 MCP get_post_analytics + get_post_comments
  → 글별 지표·단위·읽기 범위·댓글 표본 검증
  → 원문 없는 지표·감성 집계만 SQLite에 반입
  → 기존 Hermes skill compiler / 설정된 격리 native writer
  → 최신 근거와 일치하는 교훈만 리서치·기획에 전달
```

ChatGPT의 OAuth 세션을 복사하지 않습니다. 워커는 기존 비공개 `UPLOAD_POST_API_KEY`로 고정된 공식 MCP 주소에 연결합니다. 같은 키로 native 계정 ID를 별도 읽기 검증하며, 프로필 표시 이름이나 핸들만으로 소유권을 추정하지 않습니다. 원문을 JSON 파일로 복사하거나 사람이 DB에 수동 입력할 필요가 없습니다.

공식 계약: [Upload-Post MCP 연결·API 키 인증](https://docs.upload-post.com/guides/mcp-server-integration/) · [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports). 사용 API는 [댓글](https://docs.upload-post.com/api/comments)과 [글별 분석](https://docs.upload-post.com/api/get-analytics/)입니다.

## 실행과 자동 연결

저장소 루트에서 현재 안전한 차단 동작을 확인합니다.

```sh
/opt/homebrew/bin/python3.12 -m gtm --no-publish collect-mcp
```

현재 실제 결과는 `skipped / no_started_experiment`, MCP·identity 조회·모델·게시 모두 0회입니다. 이 결과를 바꾸려고 `start`, 게시 승인, 청구 검증 플래그를 켜지 않습니다.

현재 반입 상태는 [Telegram `/mcp`·`/excluded`·`/sentiment`](telegram-mcp-monitoring.ko.md)로 조회할 수 있습니다. 수집기는 고정된 결과·제외 사유·집계를 비공개 `mcp_import_events` 감사 기록에 저장합니다. 조회는 새 수집이나 skill 갱신을 실행하지 않으며, 최근 반입 이력과 현재 유효한 학습을 구분합니다.

향후 별도로 승인한 운영 환경의 한 수집 주기는 다음 명령으로 실행합니다. 비밀 파일을 shell로 source하거나 토큰을 인자로 넣지 않습니다.

```sh
python3.12 -m gtm --env-file state/runtime.env --no-publish collect-mcp
```

커밋된 설정은 `[feedback] collection_transport="mcp"`입니다. 이미 구현된 `collect` 작업이 실제 환경에서 이 값을 읽으면 MCP 반입을 자동 호출하고 별도 REST 글별 수집을 중복 실행하지 않습니다. 웹사이트 집계 등 기존 수집은 유지합니다. `collect-mcp`는 SNS 반입만 독립 점검할 때 사용합니다. 두 명령은 같은 반입 claim을 공유해 같은 구간의 중복 조회를 막습니다. 테스트용 모의 실행은 기존 REST 가짜 transport를 유지합니다.

Cloud의 기존 collect cron은 나중에 승인된 배포에서 같은 코드·설정을 실행하면 됩니다. **이번에 Cloud·cron·상시 수집을 켠 것은 아닙니다.** 미시작·미승인 게시 영수증·검토되지 않은 native 바인딩·불명확한 청구·계정 사고가 있으면 자동 수집하지 않습니다. 수집의 계정 검토는 콘텐츠 게시 승인이 아닙니다.

## 학습에 반입하는 조건

| 검사 | 제외 조건 |
| --- | --- |
| 계정·글 소유 | 현재 실험의 PUBLISHED/CONFIRMED 글, 현재 계정 지문, 검토된 native ID, 제공자 requestId와 플랫폼 글 ID가 필요. 변경된 계정·다른 실험·임의 글은 제외 |
| 신원 | 같은 키의 공식 REST 읽기에서 native ID와 인증 상태를 대조. 연결 변경·만료·누락은 중단 |
| 분석 범위 | `get_post_analytics`만 호출. 계정 전체 분석·캐시를 글별 학습으로 가져오지 않음. 반환 글 ID와 requestId 충돌 차단 |
| 지원 지표 | 명시적인 `available_metrics`가 필요. 노출 단위와 primary field가 일치해야 함. 누락·충돌·미지원 값·소수·음수·boolean·NaN은 제외 |
| 표본 노출 | 글마다 최소 100 또는 더 높게 설정한 노출 기준이 필요 |
| X 댓글 | 명시적인 `source=mentions`가 필요. 전체 답글이나 전체 여론으로 해석하지 않음 |
| Threads 답글 | 명시적인 `partial=false`가 필요. true·누락·0·문자열 false는 제외. 이는 API 접근 확인이며 모든 답글을 수집했다는 보장은 아님 |
| 댓글 정제 | 자기 답글·작성 주체 미확인·중복 ID·정규화 후 중복 문구 제외. 영어·한국어의 모호한 표현·지시문은 unknown |
| 감성 표본 | 글마다 분류 가능한 댓글 최소 5개와 분류율 50% 이상. 부족한 글은 감성뿐 아니라 이 경로의 성과 학습에서도 제외 |
| 성과 교훈 | 동일 계정·지문·플랫폼·가설·hook·분모의 글 최소 3개. 두 글·계정 합산으로 기준을 채우지 않음 |
| 신선도 | 관측은 최대 48시간. 명시적인 캐시·과거/미래 관측 시각을 차단. 최신 누적치만 사용하고 반복 관측을 합산하지 않음 |

실제 제공자의 글별 응답에 지원 지표·단위·글 ID 메타데이터가 없으면 해당 글은 계속 제외됩니다. 테스트가 통과했다고 미확인 필드를 허용하거나 기본값 0으로 채우지 않습니다. [이전 MCP 점검](upload-post-mcp-feedback.ko.md)의 계정 분석·빈 댓글은 현재도 학습 근거가 아닙니다. 한국어 개인 Threads는 보류 상태로 자동 선택하지 않으며, LinkedIn은 도구 허용 목록에 없습니다.

## 실패·중단·개인정보 보호

- MCP 클라이언트는 공식 주소 하나, 도구 2개, X·Threads만 허용합니다. 게시·예약·DM·댓글 작성·OAuth 변경·임의 URL·외부 도구·서버의 실행 요청은 처리하지 않습니다. JSON/SSE의 응답 ID·프로토콜·세션을 검증합니다.
- 1회 최대 글 10개, 글당 댓글 40개·2페이지입니다. 제공자가 limit를 무시해도 호스트에서 처리량을 자릅니다. MCP HTTP 요청 최대 32회(초기화 포함), 응답 최대 1 MiB, 요청 timeout 12초, 수집 시간 60초와 현재 진행 중인 제한 요청을 사용합니다. 리디렉션·재시도·추가 구독·유료 모델은 없습니다.
- 단일 writer lock과 기본 6시간 반입 claim을 사용합니다. I/O 전에 기존 근거를 무효화하는 null 관측을 commit하고, 성공 시 더 최신 시각의 검증된 관측으로 대체합니다. 실패·부분 조회·작업 중단이 과거의 긍정 감성을 유지하지 못하게 합니다. CLAIMED 구간은 재시작 후 자동 재시도하지 않습니다.
- 제공자/신원 오류·429가 발생하면 다음 읽기를 중단하고, 나머지 대상도 무효화하며, 실험을 PAUSED·사고 검토 상태로 두고 게시 승인을 해제합니다. 이 처리에서 예약 취소 등 외부 쓰기도 하지 않습니다. 기존 외부 작업은 운영자 검토가 필요합니다. 재개·재인증은 별도 판단 사항입니다.
- skill이 이미 생성돼 있어도 매번 최신 SQLite 근거와 일치하는지 검사합니다. 반입이나 skill 갱신 중 프로세스가 멈춰도 과거 skill을 모델에 전달하지 않습니다. 원문 댓글·작성자·게시물 native ID·키·provider 오류 원문은 반입 스냅샷·모델·Telegram·skill·공개 문서에 남기지 않습니다.
- 저장은 원문 없는 계정별 지표·폐쇄된 감성 count·상태·근거 hash만 사용합니다. skill은 비공개 runtime에서 버전·hash·출처·되돌리기 기록을 유지하고, 추가 모델 호출 없이 갱신합니다. 근거가 부족하면 교훈 없는 baseline만 생성될 수 있으며 학습 성과로 주장하지 않습니다.
- 자동 반입이 예산·게시 승인·계정 선택·플랫폼 보호 장치를 변경할 수 없습니다. 전체 $20, 모델 $5·도구 $2 내부 상한, $14 경고·$18 신규 선택 작업 중단은 유지합니다. 조회 사용량이 기존 제공자 요금제에 반영될 수 있어 확인되지 않은 청구를 0으로 확정하지 않습니다.

## 검증 결과와 실제 한계

전체 **454개**, MCP 전용 **27개** 테스트가 통과했습니다. 소스 기본값 점검·자격증명 없는 staging smoke·기존 비밀값 누출 비교 검사도 통과했습니다. [정제된 구현 점검 근거](evidence/mcp-ingestion-implementation-2026-10-25.json)에 실제 통신과 가짜 표본 검증을 구분했습니다.

테스트는 정상 X·Threads 표본 → skill → 리서치/기획 전달, 빈 댓글·부족한 표본·부분 권한·계정 변경·분모 충돌·캐시·노출 부족·원문 유출·429·구간 중복·재시작·중간 중단·과거 skill 무효화를 확인합니다. 실제 게시·모델 호출 없이 주입한 transport로 수행했습니다. 정상 학습 예시는 **가짜 테스트 표본**이며 실제 이용자 반응이 아닙니다.

실제 서버에는 기존 비공개 키로 초기화와 initialized 통신 2회만 수행해 공식 MCP 세션 연결을 확인했습니다. 실제 분석·댓글 도구는 호출하지 않았고 키를 출력하지 않았습니다. 현재 로컬 `collect-mcp`는 미시작 검사에서 외부 호출 0회로 종료됐습니다. 실제 본인 게시 영수증이 있는 Cloud 환경의 정상 학습과 Cloud 복구는 아직 검증하지 않았습니다.

구현 파일: [MCP 제한 클라이언트](../gtm/mcp_read.py) · [검증·자동 반입](../gtm/mcp_feedback.py) · [통합·보안 테스트](../tests/test_mcp_feedback.py). [README](../README.ko.md) · [마케팅 피드백](marketing-feedback.md) · [Hermes skill](hermes-auto-skills.md).

## English summary

The bounded worker now automatically reads own-post analytics/comments through official Upload-Post MCP, verifies native ownership through read-only REST, and feeds only validated aggregates into the existing Hermes skill loop. Missing permissions, sparse samples and unsupported metrics cannot train; rejection or interruption invalidates previous evidence. The existing collect job selects MCP via configuration. No Cloud instance, schedule, social post or paid model run was started. Real session initialization succeeded; positive audience-learning tests use fixtures. Current local collection safely skips because the experiment has not started. USD 20, publishing OFF and the LinkedIn block remain enforced.
