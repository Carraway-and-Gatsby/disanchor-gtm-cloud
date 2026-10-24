# Telegram 운영 현황

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
| `/status` | 실험 상태, 종료 시각(KST), 예산, 게시 집계, 라우팅 OFF/검증 차단 및 최근 작업 원인, 리서치→기획→작성→실행 단계 결과 |
| `/budget` | 누적 추정·확정·종료 시 추정 비용, 한도 경고, 청구 검증 여부 |
| `/posts` | 초안·승인·예약·게시 확인·실패·취소·외부 결과 미확인 건수 |
| `/help` | 지원하는 조회 명령 |

허용한 사용자와 개인 대화 ID가 **둘 다** 일치해야 응답합니다. Bot 발신, 채널·그룹, 전달된 메시지, 알 수 없는 명령, 명령 뒤 추가 지시는 무시합니다. Telegram 메시지는 모델이나 shell에 전달하지 않습니다. 게시·승인·일시 중지·재개·설정 변경은 지원하지 않으며, 기존 승인과 정책 검증 절차를 Cloud 콘솔에서 진행합니다.

## 자동 알림과 개인정보

실험 상태, 계정 사고 여부, 초안·승인·예약·게시 등 게시 집계, 미확인 게시 작업 수, 예산 기준 및 청구 검증, 모델 단계 결과가 달라지면 전체 상태 요약을 알립니다. 실제 환경에서 라우팅 설정이 꺼져 있으면 OFF를 표시하고, 필수 런타임·지출·출력 한도 검증이 없으면 차단으로 표시합니다. 최근 작업의 상태와 차단·중단 사유는 고정된 설명으로만 전달합니다. 작업 시각, run ID, 재조회 여부만 바뀌면 알림을 추가하지 않습니다. 매일 지정된 시각 이후 첫 실행에 한 번 KST 일일 요약을 보냅니다. 일일 요약의 시간대는 실험의 reporting_timezone과 별개로 Asia/Seoul에 고정됩니다. 첫 연결 시 같은 날 이미 보낸 전체 요약은 일일 요약을 대신합니다. 바뀐 내용이 없으면 별도 상태 메시지를 반복하지 않습니다.

종료 또는 중지 상태, 168시간 만료 시 최종 상태를 한 번 대기열에 넣고 명령 조회를 멈춥니다. 오래된 미전송 중간 보고는 종료 알림으로 대체합니다. 실험 종료는 Cloud 서버 종료와 같지 않으므로 인프라 과금은 별도 중지가 필요합니다. 계정 정지 가능성을 없애는 보장은 없으며, 기존 공식 API·일일 한도·게시 승인·사고 시 중단 규칙을 유지합니다.

메시지에는 집계와 고정 상태 설명만 포함합니다. 게시 본문, 리드 정보, 프롬프트, 모델 응답, provider 오류 원문, 사용자 메시지, 토큰은 보내지 않습니다. 제품 개발 중 표시와 초기 7일 비광고성 콘텐츠 원칙은 요약에 남깁니다. Markdown/HTML 파싱과 링크 미리보기는 끄고 `protect_content`를 요청합니다. 이 옵션은 Telegram 밖의 화면 캡처까지 막는 보안 보장은 아닙니다.

## 전달 신뢰성과 복구

SQLite의 `telegram_state`, `telegram_outbox`가 마지막 업데이트 offset, 알림 중복 방지, 전송 결과를 보존합니다. 수신 업데이트를 소비한 offset과 응답 대기열은 같은 transaction으로 저장하며, 응답 전송보다 먼저 commit합니다. 알 수 없는 입력도 offset만 갱신하고 원문은 저장하지 않습니다.

메시지를 보내기 **전** 상태를 `UNKNOWN`으로 commit합니다. API 성공과 유효한 `message_id`를 확인하면 `SENT`로 바꿉니다. 전송 timeout, 잘못된 응답, 프로세스 중단은 전달 여부를 확정할 수 없으므로 자동 재전송하지 않습니다. 메시지 중복보다 일부 알림 누락을 허용하는 설계입니다. 운영자는 Cloud의 outbox 상태와 Telegram 대화를 확인하고 새로운 `/status` 요청으로 최신 현황을 받을 수 있습니다. `UNKNOWN`을 일괄 초기화하지 않습니다.

명확한 API 429 거절만 `retry_after` 이후 재시도합니다. 과거의 모호한 전송을 재시도하는 동작은 없습니다. 한 채팅에 연속 전송을 막는 최소 1초 cooldown과 한 작업당 한 메시지 한도가 있습니다. 400/401/403/404/409 거절은 자동 재전송하지 않습니다. 토큰 오류나 Bot 차단은 Cloud 콘솔에서 수정해야 하며, Telegram 자체가 고장 나면 Telegram으로 그 장애를 알릴 수 없습니다.

종료 알림도 동일한 규칙을 따릅니다. `UNKNOWN`이면 종료 알림이 도착했다고 간주하지 않습니다. 재시작은 같은 DB를 사용해야 하며 DB를 새로 만들면 offset·알림 중복 방지 이력이 사라집니다.

## 검증 범위

주입한 가짜 transport로 승인되지 않은 발신자, 명령 주입, 중복 업데이트, 재시작, 전송 직전 중단, 모호한 전송, 명확한 429 cooldown, webhook 충돌, KST 일일 요약, 실험 종료를 테스트했습니다. 테스트는 실제 Bot API 요청이나 메시지 전송을 하지 않습니다. 실제 토큰·개인 대화 연결과 Cloud 배포 후 수신 검증은 별도 단계입니다.

공식 근거: [Telegram Bot API—요청과 응답](https://core.telegram.org/bots/api#making-requests), [getUpdates의 offset 및 webhook 제약](https://core.telegram.org/bots/api#getupdates), [sendMessage](https://core.telegram.org/bots/api#sendmessage), [429 응답 매개변수](https://core.telegram.org/bots/api#responseparameters), [Bot 전송 한도 FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this). 문서 확인일: 2026-10-24.
