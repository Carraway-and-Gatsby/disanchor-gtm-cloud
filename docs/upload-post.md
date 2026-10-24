# Upload-Post adapter contract

공식 문서를 2026-10-24에 확인했다. 이 구현은 문서와 주입한 응답으로 검증했으며, 실제 계정 인증·예약·게시·취소를 실행하지 않았다. 정상 응답과 실제 게시 여부는 라이브 환경에서 별도로 확인해야 한다. 호출 전에 상위 워크플로가 승인, 대상 계정, 게시 제한 및 중복 여부를 검사해야 한다.

## 구현 범위

`gtm.publisher.UploadPostPublisher(api_key, user, transport=None)`는 X 및 Threads의 단일 텍스트 게시물을 지원한다. 다른 플랫폼, 이미지, 영상, 댓글, DM, 자동 답글, 자동 팔로우, 조직 페이지 선택, 링크 유료 옵션은 구현하지 않는다. 미디어가 필요한 채널은 텍스트 업로드로 대체하지 않고 차단한다. 입력 내용은 요약·의역·URL 난독화하지 않는다.

`MockPublisher`는 메모리 안의 시뮬레이션이다. 같은 키와 내용은 같은 작업을 반환하고, 같은 키에 다른 내용을 넣으면 오류다. `simulated: true`가 명시되며 실제 URL, 게시 완료, 조회수, 전환 성과를 생성하지 않는다. 재시작 시 모의 상태는 사라진다. 상위 SQLite 기록과 별개의 테스트 도구다.

## 실제 API 매핑

기본 URL은 `https://api.upload-post.com/api`이며 인증은 `Authorization: Apikey <secret>`이다. 아래 경로는 기본 URL 뒤에 붙는다.

| 메서드 | HTTP 경로 | 요청과 응답 계약 |
|---|---|---|
| `schedule(platform, text, scheduled_at, idempotency_key)` | `POST /upload_text` | form 필드 `user`, `platform[]`, `title`, `scheduled_date`, `async_upload=true`, `external_id`, `request_id`; 예약 응답의 `job_id` 필요 |
| `status(job_id)` | `GET /uploadposts/status?job_id=...` | `status`, `results`; 배열 및 플랫폼별 객체 결과를 정규화 |
| `history()` | `GET /uploadposts/history?profile_username=...&page=...&limit=100` | `history[]`, `total`, `page`, `limit`; 최대 100페이지 |
| `analytics()` | `GET /analytics/{profile_username}?platforms=threads,x` | 계정별 지표; 게시물 지표와 구분 |
| `analytics(post_ids=[...])` | `GET /uploadposts/post-analytics/{request_id}` | `platforms.{platform}.post_metrics`; 입력 ID는 **Upload-Post request_id** |
| `quota()` | `GET /uploadposts/me` | `plan`, 존재할 때 `api_usage.count`, `api_usage.limit` |
| `cancel(job_id)` | `DELETE /uploadposts/schedule/{job_id}` | `success=true` 확인; 선택 필드 `credits_refunded` |

업로드 필드, form 인코딩, 예약의 `202` 및 `job_id`는 [Upload Text 공식 문서](https://docs.upload-post.com/api/upload-text/)를 따른다. 예약 시간은 시간대가 있는 ISO 8601을 받아 UTC로 바꾸고, 현재 이후 365일 이내인지 확인한다.

조회에서는 예약 `job_id`, 비동기 `request_id`, 네이티브 `platform_post_id`를 구분한다. [Upload Status](https://docs.upload-post.com/api/upload-status/)의 `completed`만 보고 연결되지 않은 플랫폼을 게시 완료로 판단하지 않는다. 개별 `skipped`를 우선하며, 처리 중인 응답과 발행 완료를 구별한다. `history()`는 [Upload History](https://docs.upload-post.com/api/upload-history/)의 `job_id`, `request_id`, `external_id`, `platform_post_id`, `post_url`을 보존한다. 응답에 다른 프로필이 섞이면 제외한다. 예약 취소는 [Manage Scheduled Posts](https://docs.upload-post.com/api/schedule-posts/)의 DELETE만 사용하며 이미 게시된 글을 삭제하지 않는다.

## 중복·장애 처리

현재 공식 문서는 업로드의 `Idempotency-Key`를 지원한다고 명시한다. 이 구현은 키를 헤더로 보내고 같은 값을 `external_id` 및 `request_id`로 전달한다. `external_id` 자체는 중복 방지 기능이 아니다. 공급자가 키를 보관하는 기간이나 동시 처리에 대한 별도의 보장은 문서에서 확인하지 않았으므로 영구적인 exactly-once 보장으로 해석하지 않는다. [Upload Text](https://docs.upload-post.com/api/upload-text/), [Rate Limits](https://docs.upload-post.com/guides/rate-limits/).

자동 재시도는 없다. POST의 시간 초과, 전송 실패, 5xx, 408/409, 해석할 수 없는 성공 응답, 누락된 `job_id`는 `AmbiguousSubmission`이다. 이미 접수되었을 수 있으므로 먼저 외부 ID와 예약 목록/이력으로 대조한다. 이 어댑터는 예약 목록 조회나 외부 ID 필터 조회를 별도 공개 메서드로 제공하지 않는다. `history()`에 아직 없는 예약은 공급자 대시보드나 공식 예약 조회 API로 확인해야 한다. 확인 전에는 새 키를 만들어 다시 보내지 않는다.

DELETE 전송 실패는 `AmbiguousCancellation`이다. 취소 확인 전에는 성공으로 기록하지 않는다. 401은 인증, 403은 권한/요금제, 429는 제한 오류로 분리한다. 응답의 숫자 `Retry-After`가 있으면 예외 속성으로 보존하지만 이 모듈은 잠들거나 재시도하지 않는다. 호출자는 429에서 신규 게시를 중단해야 한다. API 제한의 실제 헤더 및 대기 정책은 [Rate Limits 공식 문서](https://docs.upload-post.com/guides/rate-limits/)에서 다시 확인한다.

## X 무료 사용의 현실적인 제한

공식 안내는 일반 X 게시가 Free에도 포함된다고 설명한다. 무료 월 한도는 10회이며 X의 무료/Basic 일 제한은 연결 계정당 최근 24시간 10회다. 이것은 안전한 게시 빈도나 계정 정지 방지 보장이 아니다. 더 낮은 내부 빈도와 사람의 검토가 필요하다. [Upload-Post X 안내](https://www.upload-post.com/x-api-pricing/), [플랫폼별 제한](https://docs.upload-post.com/guides/limit-of-uploads/).

Upload-Post는 X Links 옵션이 없는 경우 X 게시물의 URL을 제거한다고 명시한다. 이 어댑터는 해당 유료 옵션을 지원하지 않으므로 X의 URL/도메인 형태를 사전에 거절한다. 링크를 난독화해 요금 또는 정책을 피하는 기능은 없다. [Upload Text의 X 제한](https://docs.upload-post.com/api/upload-text/).

X는 ASCII 1, 그 외 문자 2의 보수적인 가중치로 280 이내만 허용한다. 공식 X 문자 계산기의 완전한 재구현은 아니며 일부 유효한 글도 거절할 수 있다. 긴 글의 자동 스레드 분할을 막아 승인한 게시 단위를 유지하려는 제품 선택이다. Threads는 UTF-16 코드 단위 500자 이내로 검사한다.

## 지표 및 한도

반환 값은 공통 `status`, `provider_job_id`, `provider_request_id`, `external_id`, `platform_post_id`, `published_url`, `metrics`, `metric_missing_reasons`, `missing_reasons`, `simulated`를 사용한다. 없는 숫자는 `null`이다. 실제 0과 미수집을 구별한다. URL과 네이티브 게시물 ID가 없는 경우 추측하지 않는다.

게시물 지표는 `post_metrics`에서만 가져온다. 계정 누적 스냅샷을 게시물 성과로 사용하지 않는다. 링크 클릭이나 노출이 제공되지 않으면 이유와 함께 `null`로 남긴다. 리드·상담예약·지원서는 소셜 API 수치로 대체하지 않고 자체 폼/분석/CRM 귀속이 필요하다고 명시한다. [Analytics API 공식 문서](https://docs.upload-post.com/api/get-analytics/).

`quota()`는 [Current User](https://docs.upload-post.com/api/current-user/)를 호출한다. 이 페이지의 최소 응답 예에는 사용량이 없지만 [Rate Limits](https://docs.upload-post.com/guides/rate-limits/)에는 `api_usage.count`가 안내되어 있다. 따라서 사용량 또는 한도가 응답에 없으면 `remaining=null`이다. 요금제 이름만 보고 남은 한도를 추측하지 않는다. 상위 실행기는 `remaining`이 불명확하면 소유자가 검증해 설정한 보수적인 계정 잔여 한도를 사용한다. 기본값 0에서는 제출이 중단되며, 예약/불명확한 작업도 한도를 차감한다. 이 수동 기준에는 다른 도구의 사용량도 포함해야 한다.

## 테스트와 전송 경계

주입 전송 함수는 `(method, url, headers, body_bytes_or_none)`를 받아 `{"status_code": 200, "headers": {}, "body": {...}}`를 반환한다. 오류 HTTP 코드를 반드시 `status_code`로 전달한다. 전송 함수는 테스트에서만 대체하며, 비밀값과 본문을 기록하는 래퍼를 사용하지 않는다.

기본 전송은 Python 표준 라이브러리, HTTPS, 30초 타임아웃, 2 MB 응답 한도, 리디렉션 차단을 사용한다. 오류 본문은 읽지 않으며 예외 문자열은 정해진 코드만 노출한다. 테스트는 네트워크를 사용하지 않고, 예약 필드, 누락 ID, 시간 초과 중복 방지, 인증/제한 오류, skipped 상태, 페이지네이션, 지표 누락, 모의 실행과 취소를 검사한다.

```sh
python3 -m unittest discover -s tests -p test_publisher.py -v
```

라이브 검증 전에는 계정 권한, 실제 응답 구조, 무료 한도, 플랫폼 분석 권한, 과거 예약의 대조 경로, 제한 응답을 검증해야 한다. 이 파일의 테스트 통과는 실제 게시 성공이나 계정 제재가 없음을 증명하지 않는다.
