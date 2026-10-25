# Telegram 연결 준비 — Cloud 생성 없이

**전체 $20 · API 게시 OFF · LinkedIn 게시 차단 · Cloud 미생성 유지.** 이 절차는 전용 봇과 소유자 개인 대화 정보를 준비하며 실험 시작, 모델 호출, cron 설치, 메시지 전송을 하지 않습니다. Telegram 설정도 기본 OFF로 남습니다.

## 직접 해야 할 순서

1. 휴대폰 Telegram에서 공식 [@BotFather](https://t.me/BotFather)에 `/newbot`을 보내 전용 봇을 만듭니다. 이름 예: `Disanchor GTM Monitor`. 봇 사용자 이름은 사용 가능한 `…bot` 형식으로 정합니다. 다른 서비스나 Hermes 기본 gateway에서 쓰지 않는 전용 봇이어야 합니다.
2. BotFather가 발급한 **토큰은 이 채팅·GitHub·브라우저 주소창·명령어에 붙이지 않습니다.** 다음 명령을 로컬 터미널에서 실행합니다. 토큰은 실행 후 숨김 입력란에만 넣습니다. 대화 기록·화면 공유·터미널 녹화를 켜 둔 곳에서는 입력하지 않습니다.

```sh
cd /Users/cgcorp/Desktop/Development/clone-hackathon
/opt/homebrew/bin/python3.12 scripts/setup-telegram.py configure
```

다른 컴퓨터에서는 이 저장소 디렉터리에서 `python3.12 scripts/setup-telegram.py configure`를 사용합니다. Python 3.12 이상이 필요하며 추가 패키지는 없습니다.

3. 전용 봇인지 확인하는 질문에 `yes`를 입력하고, BotFather 토큰을 숨김 입력합니다. 토큰이 화면에 보이지 않는 것이 정상입니다. 도구가 공식 API에서 봇 이름과 webhook 여부를 확인합니다. 기존 webhook이 있으면 삭제하지 않고 중단합니다.
4. 도구가 알려 준 `@봇이름`의 **개인 대화**를 휴대폰에서 엽니다. 터미널에 나온 `/start gtm_…` 확인 문구를 직접 보내고, 터미널로 돌아와 Enter를 누릅니다. 봇은 아직 실행 중이 아니므로 자동 답장이 없어도 정상입니다. 확인 문구는 다른 사람에게 공유하지 않습니다.
5. 본인이 직접 보낸 개인 대화인지 확인하는 마지막 질문에 `yes`를 입력합니다. 동일한 개인 chat/user ID를 찾은 경우에만 저장합니다. 그룹·봇·전달된 메시지·문구 불일치·복수 소유자는 허용하지 않습니다. 10분 안에, 최대 3번만 확인합니다.
6. `비공개 state/runtime.env에 저장했습니다(600)`이 나오면 이 채팅에는 **“비공개 저장 완료”라고만** 알려주세요. 토큰이나 숫자 ID를 보내지 않습니다.

## 도구가 저장하는 것과 유지하는 것

- `state/runtime.env`의 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_USER_ID`만 새 값으로 준비합니다. 기존 다른 키의 값은 보존하며, 기존 Telegram 값이 있으면 명시적인 `replace` 확인 없이 덮어쓰지 않습니다. 기존 주석과 줄 순서는 재작성될 수 있습니다.
- 디렉터리 소유자·권한 `700`, 파일 소유자·권한 `600`, 일반 파일 여부를 검사하고 symlink는 거부합니다. 같은 비공개 디렉터리의 임시 파일에 기록한 뒤 원자적으로 교체합니다.
- private 설정이 도중에 바뀌면 덮어쓰지 않고 중단합니다. 파일은 shell로 실행하지 않습니다. 토큰은 명령행 인자·환경변수 전달·출력 로그로 받지 않습니다.
- API는 고정된 `api.telegram.org`의 `getMe`, `getWebhookInfo`, `getUpdates`만 허용합니다. `getUpdates`에는 offset이나 구독 변경값을 보내지 않으며, 메시지 확인만 하고 기존 대기열을 확정 소비하지 않습니다. 임의 명령·`sendMessage`·webhook 삭제는 없습니다. 리디렉션·자동 재시도도 없습니다.
- `state/`는 Git 및 Cloud 소스 패키지에서 제외됩니다. 준비한 로컬 키가 Cloud로 자동 전송되는 것은 아닙니다. 실제 Cloud 생성·키 배치는 나중에 별도로 진행합니다.
- `config.toml`의 Telegram·유료 모델·API 게시 OFF를 그대로 둡니다. LinkedIn은 여전히 전송 허용 목록 밖이고, 이 도구는 설정이나 계정 바인딩을 수정하지 않습니다.

## 저장 후 읽기 점검

```sh
/opt/homebrew/bin/python3.12 scripts/setup-telegram.py check
```

자격 증명이 없으면 네트워크 요청 없이 `credentials_pending`으로 종료합니다. 저장된 경우 `getMe`·`getWebhookInfo` 2회만 요청하고 봇 이름·webhook 부재·소유자 설정 여부만 출력합니다. 토큰·개인 숫자 ID·API 원문은 출력하지 않습니다. 이 점검은 **봇 신원 확인이며 메시지 수신·발송 성공 증거는 아닙니다.** 키가 거부되거나 webhook이 있으면 안전한 실패 메시지만 표시하고 자동 수정하지 않습니다.

실제 메시지 수신 테스트는 다음 단계로 구분합니다. 현재 이 준비 과정에서는 봇 메시지 발송, 지속 polling, Telegram 활성화를 하지 않습니다. Cloud도 만들지 않습니다. Telegram의 운영 명령은 `/status`, `/budget`, `/posts`, `/feedback`, `/help`이며 게시 승인·예산 변경·shell 실행은 지원하지 않습니다. [운영 계약](telegram.md).

## 문제가 생기면

- 토큰을 잘못 넣었거나 공개한 경우: BotFather에서 해당 토큰을 폐기·재발급하고 `configure`를 다시 실행합니다. 토큰 원문이나 오류 URL을 이 채팅에 복사하지 않습니다.
- 확인 문구를 못 찾는 경우: 정확한 봇 개인 대화와 문구를 확인합니다. 도구는 타인의 첫 메시지를 자동 소유자로 등록하지 않습니다. 대기 메시지가 많아 첫 100개에 없으면 자동으로 offset을 올리거나 대기열을 삭제하지 않고 중단합니다.
- webhook/다른 poller 충돌: 다른 서비스가 쓰는 봇을 빼앗지 말고 새 전용 봇을 사용합니다. 프로젝트 상태 봇과 Hermes 기본 gateway에 같은 토큰을 쓰지 않습니다.
- 기존 설정은 안전 점검 후에도 OFF입니다. 준비됐다는 이유로 `start`, `authorize`, `approve`, `resume`이나 Cloud 생성 명령을 실행하지 않습니다.

공식 근거: [BotFather](https://core.telegram.org/bots/features#botfather), [Bot API 읽기 메서드](https://core.telegram.org/bots/api#getme), [webhook 조회](https://core.telegram.org/bots/api#getwebhookinfo), [getUpdates와 offset](https://core.telegram.org/bots/api#getupdates). 확인일: 2026-10-25.
