# 계정별 콘텐츠 접근과 대상 확인

Upload-Post의 실제 읽기 전용 API 조회로 프로필 3개와 연결 채널 7개를 확인하고 **7개 콘텐츠 접근을 따로 준비**했습니다. 개별 프로필 조회에서 고유 ID와 `reauth_required=false`를 확인했습니다. 기본 X·Threads 후보는 추가 GET 비교 검증도 통과했습니다. 실제 ID는 Git에서 제외한 비공개 `accounts.local.toml`에만 저장했고, 공유용 [계정 레지스트리](../accounts.example.toml)는 ID를 비워 두었습니다. 모든 기록은 `reviewed=false`이며 이번 조회는 게시 승인이 아닙니다.

## 실제 연결 조회에서 확인된 범위

확인한 요금제 표시는 Basic이고 프로필은 3개였습니다. 이는 업로드 잔여량이 아닙니다. `/uploadposts/me` 응답에 사용량·잔여량이 없으므로 NULL로 남기고 설정상 허용량은 0으로 유지했습니다. `default`에는 Instagram·X·Threads, `disanchor_twitter`에는 LinkedIn·X·Threads, `disanchor_x`에는 X 연결 표시가 있었습니다. `disanchor_twitter`의 표시 이름은 `personal_important_accounts`이지만 API 프로필 이름과 혼동하지 않습니다.

| 전략 키 | Upload-Post 프로필 / 플랫폼 | 확인된 표시와 한계 | 콘텐츠 접근 |
| --- | --- | --- | --- |
| `default_instagram` | `default` / Instagram | `disanchor_com`; 연결 고유 ID 확인 | 일상적인 설정 문제를 보여 주는 장면과 캡션 초안 |
| `default_x` | `default` / X | `guidances_org`; 연결 고유 ID 확인 | 독자가 작업 문제를 직접 진단할 수 있는 짧고 유용한 점검 |
| `default_threads` | `default` / Threads | `disanchor_com`; 연결 고유 ID 확인 | 익숙한 작업 상황에서 시작하는 브랜드의 자연스러운 질문 |
| `personal_linkedin` | `disanchor_twitter` / LinkedIn | Sungyeob Lim 연결 ID 확인, UI는 Carraway & Gatsby Page 선택; 회사 Page 게시 ID 별도 검토 필요 | 업무 방식과 설계 선택을 설명하는 전문적인 초안 |
| `personal_x` | `disanchor_twitter` / X | `cgcorporation`; 연결 고유 ID 확인 | 만드는 사람 관점의 짧은 질문이나 설계상 선택 |
| `personal_threads` | `disanchor_twitter` / Threads | `technodemocracystudio`; 연결 고유 ID 확인 | 일상적인 업무에서 유용한 컴퓨팅 도구에 접근하는 문제와 열린 질문 |
| `disanchor_x` | `disanchor_x` / X | `disanchor_com`; 연결 고유 ID 확인 | 호환성·속도·설정 유지 같은 기술적 제약과 검증 방법 |

LinkedIn의 사람 이름은 로그인 또는 연결 표시일 수 있고, 선택된 회사 Page는 별도의 게시 대상입니다. 이 둘을 개인 계정 ID로 대체하지 않습니다. Page의 운영 주체와 작성자 관점이 확인되기 전에는 개인의 1인칭 경험담으로 게시하지 않습니다.

## 현재 실험에서 사용할 대상

제안한 선택은 **X의 `disanchor_x`, Threads의 `default_threads`**입니다. 실제 연결 ID 비교는 통과했지만 소유자의 게시 대상·문구 검토 전에는 게시가 활성화되지 않습니다. 설정의 `[accounts]`에서 `registry_file`, `x_account`, `threads_account`로 선택합니다. 레지스트리는 선택 가능한 전략을 설명하며, 등록된 7개 계정 모두에서 실행하라는 지시가 아닙니다.

기존 168시간 실험은 플랫폼마다 활성 대상 하나만 사용합니다. 총 10건, X·Threads 각각 최대 5건, 대상 시간대 날짜별 플랫폼당 1건이라는 한도를 그대로 유지합니다. 프로필 수만큼 게시량·예산을 늘리거나 같은 소셜 계정을 여러 프로필에 연결해 한도를 우회하지 않습니다. API 계정의 공유 할당량도 별도로 확인해야 합니다.

Instagram과 LinkedIn은 **전략·초안 준비만 지원**합니다. 현재 워커의 자동 게시 전송은 X와 Threads만 지원하며, 두 플랫폼을 추가 발견했다고 업로드 기능이나 새로운 게시 권한이 생기지 않습니다. Instagram에는 실제 이미지 생성·업로드 작업도 수행하지 않았습니다.

## 문구와 작성자 관점

브랜드 계정은 실용적인 관찰, 개인 성격의 프로필은 만드는 사람의 질문, 공식 X 전략은 기술적 제약에 초점을 둡니다. 같은 글에서 단어 몇 개만 바꾸어 여러 계정에 반복하지 않습니다. 서로 답글·좋아요·재게시를 하거나 독립적인 고객처럼 추천하는 방식으로 인위적인 확산을 만들지 않습니다.

Disanchor를 개발 중이라는 사실은 문장 안에 자연스럽게 연결합니다. 고정된 말미 고지문은 붙이지 않습니다. 개발 중이라는 사실 외에 창업자의 실제 일화, 개발 성과, 사용자 반응, 인터뷰, 경력은 근거 없이 만들지 않습니다. 초기 7일의 링크·가입·프로필 방문·구매·참여 요청 금지와 정확한 문구별 승인은 모든 전략보다 우선합니다.

각 전략의 짧은 프롬프트:

- [브랜드 Instagram](../prompts/accounts/default_instagram.md)
- [Guidances X](../prompts/accounts/default_x.md)
- [브랜드 Threads](../prompts/accounts/default_threads.md)
- [개인 프로필 LinkedIn](../prompts/accounts/personal_linkedin.md)
- [개인 X](../prompts/accounts/personal_x.md)
- [Technodemocracy Threads](../prompts/accounts/personal_threads.md)
- [공식 X](../prompts/accounts/disanchor_x.md)

## 레지스트리의 검증 범위

[로더](../gtm/accounts.py)는 알 수 없는 필드·잘못된 자료형·중복 식별자·경로 이탈·심볼릭 링크·과도한 파일 크기를 거절합니다. 프롬프트는 레지스트리 옆 `prompts/accounts/` 안의 Markdown 파일로 한정합니다. 레지스트리는 60,000바이트, 개별 프롬프트는 700바이트, 모델에 넘기는 계정별 맥락은 540바이트 이내입니다. 기본 두 대상의 맥락 합계는 JSON 기준 891바이트입니다.

`get()`과 `all()`은 미검토 기록도 보여 주고, `context()`는 계정 키·플랫폼·역할·언어·접근·말투·스타일 문구만 제공합니다. 실제 계정 ID나 검토 표시가 모델에게 권한으로 전달되지 않습니다. `binding()`은 지원 플랫폼·검토 여부·실제 ID가 모두 준비되어야 게시 대상 정보를 반환합니다. 그 반환 자체도 provider 인증 확인이나 콘텐츠 게시 승인을 대신하지 않습니다.

`fingerprint()`는 읽은 설정과 프롬프트 전체를 해시합니다. 계정 선택·연결 정보·말투 파일이 바뀌면 호출 측에서 기존 승인을 그대로 쓰지 않도록 이 지문을 승인 검사에 포함합니다. 스타일 정보는 글로벌 콘텐츠 정책 아래의 맥락일 뿐이며, 명령 실행·계정 변경·추가 게시를 지시할 수 없습니다. 실제 연결 상태는 [연결 점검 계약](upload-post-connection.md)에 따라 게시 전에 별도로 확인합니다.
