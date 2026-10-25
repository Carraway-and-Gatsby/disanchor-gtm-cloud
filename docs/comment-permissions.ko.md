# X·Threads 댓글 조회 권한 — 실제 연결 점검

**최신 상태는 [2026-10-25 17:33 KST MCP 점검](upload-post-mcp-feedback.ko.md)을 따릅니다.** 아래는 15:05~15:07 KST 최초 REST 점검의 이력입니다. 이후 개인 Threads `@technodemocracystudio`는 연결 해제·사용자 보류 상태가 되었으므로, 아래의 과거 연결·부분 조회 기록을 현재 권한으로 해석하거나 재연결 안내를 실행하지 않습니다. 공식 X·브랜드 Threads는 최신 점검에서도 최근 글이 없어 댓글 권한 미확인입니다. 최신 연결은 X 3개·브랜드 Threads 1개이며 계정 분석과 댓글 권한은 별개입니다.

2026-10-25 15:05–15:07 KST / 06:05–06:07 UTC 확인. **연결 5개 정상, X 2개 멘션 조회 성공, 개인 Threads 1개 부분 조회, 현재 선택된 X·Threads 2개는 미확인**입니다. 정상 연결과 댓글 읽기 권한은 별개입니다.

**실제 게시·예약·댓글 작성·좋아요·DM·재연결 0회. API 게시 OFF, 유료 모델 OFF, 전체 예산 USD 20 유지.** Cloud 인스턴스 생성이나 캠페인 시작도 하지 않았습니다. 기존 키를 비공개 파일에서 메모리로만 읽었습니다.

## 계정별 확인 결과

| 전략 키 · 계정 | 현재 게시 대상으로 선택 | 연결·기존 native ID | 댓글 권한 점검 결과 | 필요한 조치 |
| --- | --- | --- | --- | --- |
| `default_x` · X `@guidances_org` | 아니요 | 정상·일치 | 본인 최근 글 대상 GET 200, `source: mentions`, 반환 0개 | 멘션 조회는 확인. 전체 답글 조회로 해석하지 않음 |
| `personal_x` · X `@cgcorporation` | 아니요 | 정상·일치 | 최근 글과 사용자가 지정한 기존 글 모두 GET 200, `source: mentions`, 각 반환 0개 | 읽기 때문에 재연결할 필요는 확인되지 않음. 공식 X의 결과를 대신하지 않음 |
| `personal_threads` · Threads `@technodemocracystudio` | 아니요 | 정상·일치 | 본인 최근 글 대상 GET 200이지만 **`partial: true`**, 반환 0개 | 다른 사람 답글 읽기가 제한됨. read-replies 제공 여부 확인 후 필요 시 재연결 |
| `disanchor_x` · 공식 X `@disanchor_com` | **예** | 정상·일치 | 최근 글 목록 빈 배열 → 댓글 GET 미실행 | 기존 본인 글 ID/URL이 있으면 추가 읽기 검증. 없으면 미확인 유지 |
| `default_threads` · 브랜드 Threads `@disanchor_com` | **예** | 정상·일치 | 최근 글 목록 빈 배열 → 댓글 GET 미실행 | 기존 본인 글 ID/URL과 read-replies 권한 확인 필요. 없으면 미확인 유지 |

모든 연결의 `reauth_required=false`를 확인했습니다. X·Threads 연결 응답에는 scope·permissions·capabilities 메타데이터가 노출되지 않았습니다. 이 필드가 없다는 사실만으로 권한이 없다고 단정하지 않았습니다.

사용자가 준 URL은 `personal_x`의 기존 글입니다. 이 글도 추가 조회했으며 게시 대상을 `personal_x`로 바꾸지 않았습니다. X의 멘션 기반 응답은 특정 글의 모든 답글이나 그 글의 존재 여부를 따로 검증한 증거가 아닙니다. 0개 응답은 감성·전체 댓글 수·관심 부족의 증거도 아닙니다.

선택된 두 계정의 미확인은 **권한 거부가 확인됐다는 뜻이 아닙니다.** 최근 목록에 글이 없어서 유효한 본인 글을 대상으로 시험하지 못한 것입니다. 단지 권한을 확인하려고 테스트 글을 게시하거나 남의 글·가짜 ID를 넣지 않았습니다.

## 내가 해야 할 설정

1. **게시 OFF 유지.** `auto_publish=false`, `dry_run=true`, 허용 할당량 0, staging의 `--no-publish`·`GTM_POSTING_LOCK=1`을 유지합니다. 댓글 설정을 위해 캠페인을 시작하거나 `authorize`·`approve`·`resume`을 실행하지 않습니다. AutoDM·자동 답글·웹훅도 새로 켤 필요가 없습니다.
2. **Threads 읽기 권한 확인.** Upload-Post의 **Manage Users**에서 올바른 프로필을 고릅니다. 개인 계정은 API 프로필 `disanchor_twitter`(표시 이름 `personal_important_accounts`), 브랜드 계정은 `default`입니다. 연결된 핸들이 각각 `technodemocracystudio`, `disanchor_com`인지 대조합니다.
3. **부분 조회인 개인 Threads부터 확인.** 제공자에게 해당 연결에서 read-replies(답글 읽기) 권한을 제공하는지 확인합니다. 제공 가능한 경우 해당 계정을 재연결하고 OAuth 화면에서 답글 읽기 권한을 검토·동의합니다. `reauth_required=false`라도 새 읽기 권한이 과거 연결에 추가됐다는 뜻은 아닙니다. 이번 작업에서는 연결 해제·재연결·OAuth 동의를 실행하지 않았습니다.
4. **권한이 제공되지 않으면 기다립니다.** Upload-Post는 read-replies를 단계적으로 제공한다고 안내합니다. 사용자가 토글만 켜서 제공자 앱 승인을 대신할 수는 없습니다. 재연결을 반복하거나 권한 부족을 우회하지 말고, 부분 응답은 감성 `unknown`으로 유지합니다. 지금 API 키를 새로 만들거나 요금제를 올려야 한다는 증거는 없습니다.
5. **공식 X·브랜드 Threads의 기존 글 확인.** 이미 있는 본인 글 URL/ID가 있다면 그 글로 읽기 점검합니다. 현재 확인한 빈 목록만으로 재연결부터 할 필요는 없습니다. 기존 글이 없으면 테스트 게시 없이 미확인으로 둡니다. 본인이 플랫폼 화면에서 댓글을 볼 수 있다는 사실만으로 API 권한이 입증되지는 않습니다.
6. **설정 변경 뒤 다시 GET 검증.** 실제 native ID가 기존 비공개 바인딩과 같은지 확인하고, 본인 글 1개·댓글 1페이지로 확인합니다. Threads는 HTTP 200 외에 `partial` 여부를 확인합니다. `partial=true`는 제한 상태, `partial` 누락은 전체 읽기 미확인으로 둡니다. `partial=false`로 접근이 확인돼도 모든 댓글이 반환된다고 보장하지 않습니다. X는 계속 멘션 표본으로만 표시합니다.

재연결로 다른 계정 ID가 나오면 기존 바인딩·승인을 재사용하지 않습니다. 현재 선택은 공식 X와 브랜드 Threads 그대로이며, 조회에 성공한 개인 계정으로 자동 전환하지 않습니다. 경고·인증 오류·429가 나오면 중단하고 같은 실행에서 재시도하지 않습니다.

## 근거와 확인 범위

[공개 가능한 점검 결과](evidence/comment-permissions-2026-10-25.md)의 JSON 블록에 고정된 상태·응답 코드·집계만 담았습니다. 키·토큰·native 계정 ID·게시물/댓글 ID·댓글 원문·작성자 식별자·provider 오류 원문은 포함하지 않았습니다.

- 최초 점검: GET **11회** = 프로필 상세 3회 + 계정별 최근 글 5회 + 댓글 3회.
- 사용자가 지정한 개인 X 글 추가 점검: 프로필 대조 + 댓글 GET **2회**. **총 13회**, HTTP 200. 리디렉션·자동 재시도·페이지 추가 탐색 없음.
- 최근 글은 계정당 `limit=1`, 댓글도 `limit=1`·1페이지로 제한했습니다. 원문은 응답 구조 확인 후 폐기했으며 sentiment 분류나 skill 생성은 하지 않았습니다.
- 설정·비공개 바인딩·runtime env·기존 SQLite 파일의 전후 해시가 같았습니다. 게시 권한·할당량·예산·런타임 DB를 변경하지 않았습니다.
- 로컬에서 기존 연결을 읽기 점검한 결과이며 **실제 Hermes Cloud 호스트의 키 배치·네트워크·권한 검증을 대신하지 않습니다.** API 조회는 제공자 사용량에 반영될 수 있고, 이번 점검으로 유료 모델이나 추가 구독·인프라 구매를 시작하지 않았습니다.

## 공식 계약과 운영 시 적용

[Upload-Post Comments](https://docs.upload-post.com/api/comments)는 X를 멘션 방식으로 설명하며, Threads는 read-replies 권한이 없으면 Upload-Post를 통해 작성된 답글만 반환하고 `partial: true`를 표시한다고 안내합니다. 따라서 개인 Threads의 부분 조회는 다른 사람 반응을 충분히 읽을 수 있는 상태로 취급하지 않습니다.

[User Profiles](https://docs.upload-post.com/api/user-profiles/)의 계정 ID·재인증 상태로 연결을 대조하고, [Media List](https://docs.upload-post.com/api/instagram-media)에서 본인 기존 글 ID를 얻어 댓글 GET에 사용했습니다. 재인증 경고가 생긴 경우의 공식 안내는 [Common Errors](https://docs.upload-post.com/resources/common-errors/)를 확인하세요. read-replies 권한은 제공자 지원 여부와 해당 연결의 OAuth 동의가 함께 충족돼야 합니다.

이 수동 권한 점검이 과거 글을 실험 DB에 편입하는 것은 아닙니다. 운영 수집기는 여전히 로컬 게시 영수증으로 확인된 글만 읽고, 모델·Telegram·skill에는 검증된 집계만 전달합니다. 부분 조회·미확인은 추측으로 채우지 않습니다. 실제 배포 때도 $20 전체 상한, $14 경고·$18 신규 선택 작업 중단, 168시간 종료와 별도 Cloud 정지 확인을 유지합니다.

[README](../README.ko.md) · [7일 계획](seven-day-schedule-and-feedback.ko.md) · [연결 점검 계약](upload-post-connection.md) · [반응·skill 기준](marketing-feedback.md) · [배포 체크리스트](deployment-checklist.ko.md)
