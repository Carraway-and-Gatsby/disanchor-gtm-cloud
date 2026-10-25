# Telegram 운영 현황

Cloud 생성 없이 먼저 준비할 때는 [한국어 봇 연결 준비](telegram-setup.ko.md)를 따릅니다. 숨김 토큰 입력·개인 대화 확인·비공개 저장만 수행하고 Telegram·게시·유료 모델은 OFF로 유지합니다.

Cloud worker와 SQLite가 운영 상태의 원본입니다. Telegram은 운영자가 현재 상태를 확인하는 기본 창구이며, HTML 화면은 콘텐츠·결과 검토용입니다. 실제 상태에서도 승인·계정·예산·게시 설정 조건을 충족하지 않으면 `게시 OFF`로 표시합니다. 모의 실행 DB는 기본 실제 Telegram transport로 보낼 수 없습니다. 상태 메시지는 고정된 한국어 템플릿으로 만들므로 모델 호출과 추론 비용이 없습니다. 모델 작업의 단계별 배분은 [모델 라우팅](model-routing.md)을 참고하세요.

## 설정과 실행

기본 설정은 비활성입니다. 소유자가 생성한 전용 Telegram Bot의 토큰과 본인의 **숫자형 private chat ID 및 user ID**를 Cloud의 비밀 환경변수에 넣습니다. 개인 대화에서는 두 ID가 같아야 합니다. 계정 이름, 채널, 그룹은 지원하지 않습니다. BotFather에서 Bot을 만든 뒤 소유자가 먼저 Bot과 개인 대화를 시작해야 합니다. 비밀값을 저장소, 프롬프트, 명령행 인자, 공유 보고서에 넣지 않습니다.

```text
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_CHAT_ID=<positive numeric private chat ID>
TELEGRAM_USER_ID=<same allowlisted numeric user ID>
```

```toml
[telegram]
enabled = true
daily_hour = 9
```

```sh
python -m gtm --config config.toml --state state/gtm.sqlite telegram
```

이 명령은 한 번의 제한된 조회·알림 작업을 실행하고 종료합니다. Cloud 스케줄러에서 같은 상태 파일과 단일 writer lock을 사용해 반복 실행합니다. 조회는 `getUpdates`의 5초 long poll이며 HTTP timeout은 12초입니다. 한 번 실행할 때 최대 한 메시지만 보냅니다. 스케줄 간격보다 응답이 느려질 수 있으며 여러 요청은 다음 실행까지 대기합니다. 병렬 worker나 동일 Bot을 사용하는 다른 `getUpdates` 소비자를 띄우지 않습니다. 설정된 webhook이 있으면 오류를 보고하며 자동으로 지우거나 전환하지 않습니다.

## 운영자 조회 명령

| 명령 | 응답 |
| --- | --- |
| `/status` | 실험 상태, 종료 시각(KST), 예산, 게시 집계, 라우팅 OFF/검증 차단 및 최근 작업 원인, 모델 단계 결과, 반응·학습 요약 |
| `/budget` | 누적 추정·확정·종료 시 추정 비용, 한도 경고, 청구 검증 여부 |
| `/posts` | 초안·승인·예약·게시 확인·실패·취소·외부 결과 미확인 건수 |
| `/drafts` | 7개 계정의 운영 후보·보류·차단 구분과 계정별 조회 명령 |
| `/drafts default_threads` | 운영 후보 Threads `@disanchor_com`의 영어 검토 초안 2개 |
| `/drafts personal_threads` | 운영·재연결 보류 Threads `@technodemocracystudio`의 한국어 검토 초안 2개 |
| `/feedback` | 최근 관측 글·비교 가능 그룹 수, 글별 댓글 표본 판정, 참고 순위, 최근 skill 생성 방식과 상태 |
| `/help` | 지원하는 조회 명령 |

허용한 사용자와 개인 대화 ID가 **둘 다** 일치해야 응답합니다. Bot 발신, 채널·그룹, 전달된 메시지, 알 수 없는 명령, 명령 뒤 추가 지시는 무시합니다. Telegram 메시지는 모델이나 shell에 전달하지 않습니다. 게시·승인·일시 중지·재개·설정 변경은 지원하지 않으며, 기존 승인과 정책 검증 절차를 Cloud 콘솔에서 진행합니다.

`/drafts`에 표시한 정확한 계정 키 하나만 인수로 허용합니다. 경로·추가 지시·여러 명령을 넣으면 무시합니다. 초안은 공개 [검토 문서](account-drafts.en-ko.md)에서 읽으며 운영 DB의 생성 초안·모델 원문을 보내지 않습니다. 문서 전체 SHA-256을 코드의 검토 버전과 비교하고, 언어·개발 중 고지·중복·광고 문구를 X·Threads 본문에 검사합니다. 문서를 바꾸면 검토 후 `gtm/review_drafts.py`의 hash도 갱신해야 하며, 불일치하면 본문 전송을 차단합니다. LinkedIn·Instagram 문구는 보관용 조회일 뿐 게시 권한이 생기지 않습니다.

## Cloud 생성 전 로컬 연결 테스트

소유자가 실제 Telegram 전송을 요청한 경우에만 저장소에서 실행합니다.

```sh
/opt/homebrew/bin/python3.12 scripts/test-telegram.py --seconds 300
```

토큰은 기존 `state/runtime.env`에서 비공개로 읽고, 다른 API 키를 환경에 넣거나 사회관계망 클라이언트를 만들지 않습니다. 기존 실험이 미시작·미승인 상태이고 $20·게시 OFF·할당량 0·유료 모델 OFF·Cloud용 Telegram OFF 설정일 때만 실행합니다. Cloud·cron·영구 설정을 생성하거나 활성화하지 않습니다. 처음에 로컬 상태와 브랜드 Threads 검토 초안을 보내며, 이후 본인 개인 대화의 명령만 처리합니다.

휴대폰에서 `/status`, `/drafts default_threads`, `/drafts personal_threads`를 각각 보냅니다. 세 명령의 응답 전송이 확인되거나, 최대 5분(옵션으로 최대 10분)·8회 전송 시도·오류에 도달하면 종료합니다. 같은 상태 DB와 단일 writer lock을 사용하고, Telegram 외 네트워크 목적지와 webhook 변경·유료 broadcast를 차단합니다. 이전 미전송 Telegram 대기열이 있으면 새 테스트를 시작하지 않습니다. 결과 메타데이터는 Git에서 제외된 `state/telegram-test-result.json`에 저장합니다. Bot API 성공은 휴대폰 화면 확인과 구분합니다. 실험·게시 초안·게시 작업·비용 원장은 변경하지 않으며 Telegram 수신 offset·응답 대기열만 저장합니다. 종료 후에는 명령에 계속 응답하지 않습니다.

## `/feedback` 읽는 방법

`/feedback`은 SQLite에 이미 저장된 지표·댓글 집계와 학습 이벤트를 읽습니다. 요청할 때 Upload-Post를 새로 조회하거나 모델을 호출하지 않고, skill 생성·갱신도 실행하지 않습니다. Telegram 명령을 받고 응답하는 Bot API 통신만 사용합니다. 실제 데이터 수집과 학습 갱신은 Cloud의 `collect` 작업이 담당합니다. 따라서 명령 응답이 도착했다는 사실은 최신 외부 데이터 수집 성공을 뜻하지 않습니다.

최근 관측은 현재 선택된 계정과 일치하는 바인딩의 자료만 사용합니다. 계정·플랫폼·노출 지표가 같은 그룹 안에서 비교하며, 최신 누적 스냅샷만 사용하고 48시간이 지난 관측은 제외합니다. 기본 조건은 글마다 최소 100회 노출, 그룹마다 최소 3개 글입니다. 조건을 충족한 그룹 중 최대 2개를 표시하고 각 그룹의 최대 3개 로컬 게시 ID를 참고 순위로 보여 줍니다. 분모가 다른 조회·도달·노출 수를 하나의 순위에 섞지 않습니다. 이 순위는 관측된 반응을 비교하기 위한 것으로, 인과적 우승·통계적 유의성·다음 글의 성공을 뜻하지 않습니다.

`댓글 표본 판정(글 수): 긍정 2`는 **각자 가져온 댓글 표본이 긍정으로 분류된 글이 2개**라는 뜻입니다. 댓글 2개, 사람 2명, 전체 이용자의 긍정 비율을 뜻하지 않습니다. 긍정·부정·혼합·미확인은 글 단위 집계입니다. 댓글이 없거나, 권한이 불완전하거나, 키워드 분류에 충분한 표본이 없으면 미확인으로 남깁니다. 좋아요를 긍정 감정으로 대체하지 않습니다. 자세한 표본·언어·플랫폼 제한은 [마케팅 피드백](marketing-feedback.md)을 확인하세요.

`학습 스킬`에는 최근 기록된 생성 결과와 방식을 표시합니다.

| 표시 | 의미 |
| --- | --- |
| `created` / `updated` / `unchanged` | skill 생성·갱신·동일 근거 유지의 최근 기록 |
| `disabled` / `unavailable` / `no_record` | 비활성·검증 또는 실행 확인 필요·기록 없음 |
| `Hermes native skill_manage` | 설정된 격리 runtime의 native 도구로 생성·수정한 기록 |
| `native 형식 생성` | native 도구 없이 compiler가 호환 형식으로 작성한 기록 |
| `실행 기록 없음` | 확인할 수 있는 native/compiled 생성 방식이 없음 |

최근 생성 기록은 skill의 효과, 현재 모델 사용 가능 여부, Cloud 배포 완료를 보증하지 않습니다. 실제 리서치·기획에 넣을 때 별도로 정책·계정 바인딩·내용 hash·유효 기간을 검사합니다. Telegram에서 skill을 수정하거나 게시 권한을 부여할 수 없습니다. [Hermes auto-skill 안내](hermes-auto-skills.md)에 생성 방식과 검증·되돌리기를 설명합니다.

## 자동 알림과 개인정보

실험 상태, 계정 사고 여부, 초안·승인·예약·게시 등 게시 집계, 미확인 게시 작업 수, 예산 기준 및 청구 검증, 모델 단계 결과, 반응 집계·참고 순위·학습 결과가 달라지면 전체 상태 요약을 알립니다. 실제 환경에서 라우팅 설정이 꺼져 있으면 OFF를 표시하고, 필수 런타임·지출·출력 한도 검증이 없으면 차단으로 표시합니다. 최근 작업의 상태와 차단·중단 사유는 고정된 설명으로만 전달합니다. 작업 시각, run ID, 재조회 여부만 바뀌면 알림을 추가하지 않습니다. 매일 지정된 시각 이후 첫 실행에 한 번 KST 일일 요약을 보냅니다. 일일 요약의 시간대는 실험의 reporting_timezone과 별개로 Asia/Seoul에 고정됩니다. 첫 연결 시 같은 날 이미 보낸 전체 요약은 일일 요약을 대신합니다. 바뀐 내용이 없으면 별도 상태 메시지를 반복하지 않습니다.

종료 또는 중지 상태, 168시간 만료 시 최종 상태를 한 번 대기열에 넣고 명령 조회를 멈춥니다. 오래된 미전송 중간 보고는 종료 알림으로 대체합니다. 실험 종료는 Cloud 서버 종료와 같지 않으므로 인프라 과금은 별도 중지가 필요합니다. 계정 정지 가능성을 없애는 보장은 없으며, 기존 공식 API·일일 한도·게시 승인·사고 시 중단 규칙을 유지합니다.

자동 상태 메시지에는 집계, 로컬 계정·게시 전략 ID와 고정 상태 설명만 포함합니다. 소유자가 `/drafts`로 요청할 때만 버전을 검증한 공개 검토 초안 본문을 전송합니다. 운영 DB의 생성 초안, 댓글 원문·작성자 식별자, 리드 정보, 프롬프트, skill 원문, 모델 응답, provider 오류 원문, 사용자 메시지, 토큰은 보내지 않습니다. 제품 개발 중 표시와 초기 7일 비광고성 콘텐츠 원칙은 요약에 남깁니다. Markdown/HTML 파싱과 링크 미리보기는 끄고 `protect_content`를 요청합니다. 이 옵션은 Telegram 밖의 화면 캡처까지 막는 보안 보장은 아닙니다.

## 전달 신뢰성과 복구

SQLite의 `telegram_state`, `telegram_outbox`가 마지막 업데이트 offset, 알림 중복 방지, 전송 결과를 보존합니다. 수신 업데이트를 소비한 offset과 응답 대기열은 같은 transaction으로 저장하며, 응답 전송보다 먼저 commit합니다. 알 수 없는 입력도 offset만 갱신하고 원문은 저장하지 않습니다.

메시지를 보내기 **전** 상태를 `UNKNOWN`으로 commit합니다. API 성공과 유효한 `message_id`를 확인하면 `SENT`로 바꿉니다. 전송 timeout, 잘못된 응답, 프로세스 중단은 전달 여부를 확정할 수 없으므로 자동 재전송하지 않습니다. 메시지 중복보다 일부 알림 누락을 허용하는 설계입니다. 운영자는 Cloud의 outbox 상태와 Telegram 대화를 확인하고 새로운 `/status` 요청으로 최신 현황을 받을 수 있습니다. `UNKNOWN`을 일괄 초기화하지 않습니다.

명확한 API 429 거절만 `retry_after` 이후 재시도합니다. 과거의 모호한 전송을 재시도하는 동작은 없습니다. 한 채팅에 연속 전송을 막는 최소 1초 cooldown과 한 작업당 한 메시지 한도가 있습니다. 400/401/403/404/409 거절은 자동 재전송하지 않습니다. 토큰 오류나 Bot 차단은 Cloud 콘솔에서 수정해야 하며, Telegram 자체가 고장 나면 Telegram으로 그 장애를 알릴 수 없습니다.

종료 알림도 동일한 규칙을 따릅니다. `UNKNOWN`이면 종료 알림이 도착했다고 간주하지 않습니다. 재시작은 같은 DB를 사용해야 하며 DB를 새로 만들면 offset·알림 중복 방지 이력이 사라집니다.

## 검증 범위

주입한 가짜 transport로 승인되지 않은 발신자, 명령 주입, 중복 업데이트, 재시작, 전송 직전 중단, 모호한 전송, 명확한 429 cooldown, webhook 충돌, KST 일일 요약, 실험 종료를 테스트했습니다. 테스트는 실제 Bot API 요청이나 메시지 전송을 하지 않습니다. 실제 토큰·개인 대화 연결과 Cloud 배포 후 수신 검증은 별도 단계입니다.

## English operator summary

The owner-only bot supports `/status`, `/budget`, `/posts`, `/drafts`, `/drafts <allowlisted account key>`, `/feedback` and `/help`. Draft review reads only a pinned public review document, never the generated publishing queue. Korean Technodemocracy Threads remains on hold; LinkedIn stays blocked. `/feedback` reads existing local SQLite aggregates and the latest recorded skill event. It makes no new Upload-Post request, model call or skill update; only normal Telegram polling and delivery use the network. Cloud's `collect` job refreshes observations separately.

Rankings are directional comparisons within the same account identity, platform and exposure denominator, with minimum evidence thresholds. The bot shows up to two groups and three local post IDs per group. Sentiment totals count **posts whose available comment samples received each label**, not people, comments or the whole audience. Missing or ambiguous evidence remains unknown.

`Hermes native skill_manage` identifies a recorded native-tool write; `native 형식 생성` identifies compiler output in the compatible format. Neither proves marketing effectiveness or Cloud deployment. Policy, account identity, skill integrity and freshness are checked again before lessons enter planning. The bot cannot publish, approve, change budgets, execute code or create skills, and it sends no raw comments, author identities, credentials or skill text.

공식 근거: [Telegram Bot API—요청과 응답](https://core.telegram.org/bots/api#making-requests), [getUpdates의 offset 및 webhook 제약](https://core.telegram.org/bots/api#getupdates), [sendMessage](https://core.telegram.org/bots/api#sendmessage), [429 응답 매개변수](https://core.telegram.org/bots/api#responseparameters), [Bot 전송 한도 FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this). 문서 확인일: 2026-10-24.
