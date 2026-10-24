# Upload-Post 계정 연결 점검

계정 목록은 인증된 조회 결과로 확인합니다. API 키가 없거나 조회가 실패하면 실제 연결 계정·핸들을 확인했다고 표시하지 않습니다. 연결 점검은 게시, OAuth 시작, 계정 연결·해제, 프로필 이름 변경을 실행하지 않습니다. 기존 개발 중 고지, 비광고성 초기 콘텐츠, 게시 승인과 한도는 계속 적용합니다.

## 공식 조회 계약

확인일: 2026-10-24. 기본 주소는 `https://api.upload-post.com/api`, 인증 헤더는 `Authorization: Apikey <secret>`입니다.

| 조회 | 응답에서 사용하는 범위 |
| --- | --- |
| `GET /uploadposts/users` | `success`, `profiles[]`, `plan`, `limit` |
| `GET /uploadposts/users/{profile_username}` | `success`, `profile` |
| `GET /uploadposts/me` | 인증 여부, 요금제 및 제공되는 사용량 |

`profiles[].username`은 게시 요청의 `user`에 넣는 Upload-Post 프로필 식별자입니다. 프로필 하나 아래 X와 Threads가 각각 연결됩니다. `social_accounts.<platform>.username`은 플랫폼 계정의 불투명 식별자이며, `handle`은 공개 이름, `display_name`은 표시용 이름입니다. 표시 이름을 연결 식별자로 사용하지 않습니다. 계정 값은 객체·빈 문자열·null일 수 있고, `reauth_required=true`면 재인증이 필요합니다. 세부 필드가 없는 오래된 응답에서 계정 ID나 정상 인증 상태를 추측하지 않습니다. [User Profiles 공식 문서](https://docs.upload-post.com/api/user-profiles/), [OpenAPI](https://docs.upload-post.com/openapi.json).

`/users`의 `limit`은 **프로필 개수 한도**입니다. 월 업로드 잔여량이 아닙니다. `/me` 최소 응답에는 사용량이 없고, 사용량 안내는 `api_usage.count`를 설명합니다. 사용량 또는 업로드 한도가 빠져 있으면 잔여량은 미확인으로 남깁니다. 계정 이메일과 provider 메시지 원문은 연결 보고서에 필요하지 않습니다. [Current User](https://docs.upload-post.com/api/current-user/), [사용량과 API 제한](https://docs.upload-post.com/guides/rate-limits/).

플랫폼 게시 한도는 실제 연결된 소셜 계정 단위이며 예약 실행 시 다시 검사됩니다. 같은 소셜 계정을 여러 프로필에 연결했다고 안전 한도나 월 할당량이 늘어나는 것으로 해석하지 않습니다. 이 실험은 더 낮은 내부 일일 한도를 유지합니다. [플랫폼별 업로드 제한](https://docs.upload-post.com/guides/limit-of-uploads/).

## 점검 모듈

`gtm.account_connection.UploadPostAccountClient(api_key, transport=None)`는 `profiles()`, `current_user()`, `verify_binding(profile_username, platform, native_account_id)`만 제공합니다. 내부 요청은 GET으로 제한됩니다. `check_connection(api_key=None, bindings=None, transport=None)`은 환경변수에서 키를 자동으로 가져오지 않으며 호출자가 명시적으로 전달합니다.

정규화한 계정 행은 `profile_username`, `platform`, `native_account_id`, `handle`, `display_name`, `connection_state`, `supported`로 구성합니다. X·Threads가 빠져 있어도 미연결 행을 표시하고, 그 외 알려진 채널은 조회 대상으로만 표시합니다. 모듈의 `connected`는 실제 ID와 명시적인 `reauth_required=false`가 모두 있을 때만 사용합니다. 응답에 재인증 필드가 없으면 `authentication_unverified`, ID가 없으면 `identity_unverified`이므로 바인딩 검증은 차단됩니다. 이 판정은 불완전한 응답을 보수적으로 다루는 프로젝트 정책입니다.

| 전체 결과 | 뜻 |
| --- | --- |
| `disabled` | 키가 없어 요청을 하지 않음 |
| `needs_binding` | 목록은 조회했으나 명시적으로 지정한 게시 대상 바인딩이 없음 |
| `verified_read_only` | 지정한 프로필·플랫폼·실제 ID와 연결 상태를 조회로 확인함 |
| `blocked` | 인증·응답·바인딩·재인증·대상 불일치 등을 해결해야 함 |

모든 결과는 `posts_sent=0`과 실제 시도한 `network_calls`를 포함합니다. `verified_read_only`도 게시 승인이나 잔여 할당량 확인을 뜻하지 않습니다. 월 사용량은 별도 `quota`에 표시하며 누락되면 `remaining=null`입니다. 대상 목록을 지정할 때는 각 항목에 `id`, `upload_post_profile`, `platform`, `native_account_id`를 명시합니다. 같은 계정 ID를 같은 플랫폼의 여러 프로필 바인딩으로 중복 등록하면 조회 전 차단합니다.

## 계정 식별과 운영 접근

연결 검토에서는 Upload-Post 프로필, 플랫폼, 실제 계정 ID, 공개 핸들, 연결·재인증 상태를 구분합니다. 같은 프로필 아래 계정이 교체되었을 때 이전 승인을 그대로 사용하지 않도록 실제 계정 ID를 비교해야 합니다. 핸들이나 표시 이름만 바뀐 경우에도 사람이 현재 대상을 쉽게 확인할 수 있도록 새 조회 결과를 표시합니다.

계정별 접근은 확인한 계정에 연결된 별도 설정으로 관리합니다. 채널을 발견했다는 이유로 자동 게시를 활성화하거나, 지원하지 않는 플랫폼에 X·Threads 텍스트를 그대로 보내지 않습니다. 동일한 콘텐츠를 여러 계정에 복제하거나 한도를 여러 프로필로 나누어 우회하지 않습니다. 계정별 성과와 소재는 분리하고, API 키가 공유하는 할당량과 실험 전체 예산은 함께 제한합니다.

조회 결과에는 OAuth 토큰, 비밀 연결 URL, 프로필 이미지 URL, API 키, 제공자 오류 원문을 저장하지 않습니다. 보고서를 열어 대상을 확인한 뒤 승인된 설정에 반영합니다. 실제 키와 Cloud 접속이 준비되지 않았다면 문서·모의 테스트가 통과해도 연결 성공으로 기록하지 않습니다.

## 점검이 확인하는 것

점검 결과는 요청 시점의 연결 상태입니다. 이후 재인증 만료·계정 교체·provider 장애가 발생할 수 있으며 게시 성공이나 계정 정지 방지를 보장하지 않습니다. 실제 전송 직전 계정 식별자, 승인, 연결 상태와 게시 제한을 다시 확인해야 합니다. API 오류에서는 새 연결 생성이나 게시를 시도하지 않고 안전한 상태 코드와 확인 필요 항목만 남깁니다.

## 계정 바인딩을 적용한 게시 어댑터

`AccountBoundPublisher(api_key, registry, targets, transport=None)`는 X와 Threads의 선택 대상을 각각 명시합니다. `targets`는 두 플랫폼 키와 로컬 계정 ID만 받아들이며, 각 ID의 registry 플랫폼이 일치해야 합니다. 두 채널이 서로 다른 Upload-Post 프로필에 속해도 각각의 프로필로 요청합니다.

`verify_destination(platform)`은 사람이 검토한 바인딩과 실제 계정 ID를 요구하고 해당 프로필을 새로 조회합니다. 결과는 고정된 `binding`과 정규화한 `receipt`입니다. ID 변경, 재인증 필요, ID·인증 상태 미확인, 로컬 registry 변경에서는 `account_identity_changed` 오류로 중단합니다. 인증·권한·API 제한 오류는 기존 안전한 오류 유형을 유지합니다.

`AccountBoundPublisher.schedule()`은 입력을 검증하고 실제 POST 바로 앞에서 목적지를 다시 조회합니다. 이 어댑터는 게시 승인을 만들지 않으며, host의 콘텐츠 검토·승인·한도·중복 차단 조건을 통과한 요청에만 사용합니다. 조회와 provider 제출은 원자적인 작업이 아니므로 두 요청 사이에 계정을 바꾸는 경쟁 조건까지 없앨 수는 없습니다. 운영 중 대상 연결을 교체하지 않고, 변경할 때 중지·대조·재승인 절차를 따릅니다.

할당량은 API 계정 전체에서 한 번 조회하며 프로필별로 더하지 않습니다. 게시 이력은 선택된 고유 프로필마다 조회하고 해당 프로필에서 선택한 채널만 합칩니다. 중복 이력은 제거하며 동일한 작업 ID의 내용이 충돌하면 대조를 중단합니다. 게시별 분석은 명시적인 Upload-Post request ID만 받습니다. 이미 접수된 작업의 상태 조회와 취소는 현재 바인딩이 미검토 상태여도 가능하므로 계정 변경을 발견한 뒤에도 외부 예약을 정리할 수 있습니다.

## 로컬 검증

```sh
python3.12 -m unittest discover -s tests -p test_account_connection.py -v
python3.12 -m unittest discover -s tests -p test_bound_publisher.py -v
```

테스트는 가짜 transport만 사용합니다. 실제 계정·키·핸들은 시험 데이터로 대체하며, GET 전용 요청, 키 누락 시 무호출, 표시 이름과 실제 ID 분리, 계정 교체, 재인증, 중복 대상, 불명확한 할당량과 민감 필드 제외를 확인합니다. 계정 바인딩 어댑터 테스트의 POST·DELETE도 가짜 transport에서만 실행됩니다. 테스트 통과는 실제 연결 성공을 뜻하지 않습니다.
