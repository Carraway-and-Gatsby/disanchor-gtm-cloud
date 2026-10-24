# Cloud 운영과 Telegram 현황 확인

운영 기준은 Cloud의 SQLite 상태와 비용 원장이다. HTML 화면은 검토용 스냅샷이며 운영에 브라우저나 로컬 Mac이 필요하지 않다. Telegram은 개인 운영자에게 상태를 전달하는 창구다. 이 문서는 배포 절차이며, 실제 Cloud 배포·모델 호출·Telegram 전송이 완료됐다는 증거가 아니다.

## 실행 구조

`daily`는 리서치 → 기획 → 작성 → 실행 검토 순서의 모델 라우팅을 거친 뒤 기존 게시 검증을 적용한다. 리서치·기획에 더 많은 예산을 주고, 작성·실행 검토는 작은 모델과 짧은 출력으로 제한한다. 모델 응답은 구조화된 제안이며 게시 권한을 부여하지 않는다. 초반 공감·관찰 중심 콘텐츠, 개발 중 고지, 광고·링크·참여 유도 제한도 그대로 적용한다. 정확한 모델·단계별 예산·활성화 조건은 설정과 모델 라우팅 문서를 검토한다.

| Cloud 작업 | 주기 / 최대 실행 횟수 | 역할 |
| --- | --- | --- |
| `daily` | 24시간 / 7회 | 단계별 라우팅과 승인된 게시 처리 |
| `collect` | 8시간 / 21회 | 확인 가능한 지표 수집 |
| `reconcile` | 4시간 / 42회 | 외부 예약·게시 상태 확인 |
| `watchdog` | 1시간 / 168회 | 종료·비용·운영 보호 조건 확인 |
| `report` | 24시간 / 7회 | 참고용 보고서 출력 |
| `telegram` | 5분 / 2,017회 | 개인 명령 확인, 변경 알림과 일일 요약 |

모두 `--no-agent --deliver local` 작업이다. Hermes가 스케줄러 자체의 LLM 세션을 만들지는 않지만, `daily` 내부에서 활성화된 모델 단계는 비용이 들 수 있다. Telegram은 전용 Bot API 클라이언트가 보내므로 Hermes의 별도 Telegram 전달을 겹쳐 켜지 않는다. 공식 [script-only cron 문서](https://hermes-agent.nousresearch.com/docs/guides/cron-script-only)는 스크립트 경로와 인터프리터 제한, provider 자격 증명 미상속을 설명한다. 래퍼는 Hermes 스크립트 디렉터리에서 시작한 뒤 이 프로젝트의 Python 3.12+로 전환한다.

## 배포 확인 순서

1. [Hermes Cloud](https://portal.nousresearch.com/cloud)에서 Small의 실제 사양·현재 가격·정지 후 저장 비용을 확인한다. 프로젝트, `.venv`, 설정, `state/gtm.sqlite`, `state/runtime.env`, 보고서를 인스턴스의 영속 저장소에 둔다. 단일 작업자와 동일한 Hermes 사용자/프로필로 운영한다.
2. `config.toml`을 검토하고 게시·모델·Telegram을 비활성 상태로 초기 점검한다. 모델은 실제 Cloud 버전, 단계별 provider/model, provider 측 출력·지출 한도와 비용 정산 검증 후 활성화한다. 로컬 CLI capability 검사는 이 검증을 대신하지 않는다.
3. 자격 증명은 `state/runtime.env`에 따옴표 없는 `KEY=value`로 저장하고 권한을 `600`으로 설정한다. 파일은 작업자 소유의 일반 파일이어야 한다. 허용 키는 `UPLOAD_POST_API_KEY`, `CLONE_APP_KEY`, `CLONE_USER_ID`, `GA4_ACCESS_TOKEN`, `GOOGLE_APPLICATION_CREDENTIALS`, `GA4_PROPERTY_ID`, `GTM_LEADS_AGGREGATE_PATH`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_USER_ID`다. 파일을 shell로 source하지 않는다. 모델 자격 증명은 Hermes의 별도 설정에서 관리한다.
4. 초기화와 실험 시작 시각 기록, 계정·게시 후보 검토, 비용 검증을 기존 [운영 안내](../README.md)에 따라 진행한다. 시작 시각은 168시간 종료 경계의 기준이며 재시작할 때 변경하지 않는다.
5. 실제 Cloud에서 설치 명령을 실행한다. `GTM_EXPECTED_HERMES_VERSION`에는 직접 검토한 `hermes --version` 첫 줄 전체를 넣는다. 아래 `--check`는 파일이나 cron을 만들지 않는다.

```sh
bash scripts/hermes-install.sh --check
hermes --version
hermes cron create --help
export GTM_HOSTED_INSTANCE_CONFIRMED=yes
export GTM_EXPECTED_HERMES_VERSION='검토한 실제 버전 첫 줄'
export GTM_PYTHON="$PWD/.venv/bin/python"
bash scripts/hermes-install.sh --install
hermes cron list
hermes cron status
```

6. 반환된 **6개 작업 ID**를 보관한다. 간격 작업은 즉시 실행되지 않으므로 첫 작업은 시작 시각 기록 후 수동으로 실행한다. `hermes cron run JOB_ID`의 동작과 관리 명령은 설치된 버전의 help 및 [CLI 문서](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)로 확인한다. 재설치는 `disanchor-gtm-` 작업이나 기존 래퍼를 발견하면 중단한다. 부분 설치 시 이번 실험에서 생성된 ID와 래퍼만 확인·정리한다.
7. Cloud 재시작 후 실험 ID·시작 시각·게시 ID가 유지되는지 확인한다. 호스팅된 gateway/스케줄러가 살아 있는 상태에서 Mac을 꺼도 다음 작업과 Telegram 응답이 도착하는지 검증한다. 서버가 멈추면 같은 서버의 Telegram 작업도 알리지 못하므로 메시지가 없다는 사실을 정상 운영의 증거로 보지 않는다.

## Telegram 연결

전용 봇을 만들고 운영자가 봇의 개인 채팅을 먼저 시작한다. `[telegram]` 설정을 활성화하기 전에 봇 토큰, 개인 채팅 ID, 운영자 사용자 ID를 위 비공개 환경 파일에 넣는다. 개인 채팅과 사용자 allowlist가 모두 일치해야 응답한다. 그룹·채널·다른 사용자의 메시지는 명령으로 처리하지 않는다. 같은 봇을 다른 `getUpdates` poller나 Hermes의 일반 대화용 Telegram gateway에 동시에 연결하지 않는다. 기존 webhook이 있다면 명시적으로 정리한 후 polling을 검증한다.

기본값은 `enabled = false`, `daily_hour = 9`다. 일일 요약 시각은 실험의 `reporting_timezone`과 별개로 `Asia/Seoul`(KST)에 고정된다. 활성화 후 해당 시각을 지난 첫 정상 poll에서 하루 한 번 요약한다.

운영자 명령은 `/status`, `/budget`, `/posts`, `/help`다. Telegram 메시지로 게시 승인·설정 변경·실행 중단·재시작·shell 실행을 요청할 수 없다. 상태 메시지는 집계 정보와 정해진 상태값을 사용하며 토큰·연락처·원문 로그를 보내지 않는다. 모델 라우팅 OFF·검증 미완료·최신 작업의 차단/중단 원인과 게시 초안→승인→예약→게시 집계 변화를 알린다. 원인은 고정된 안전한 설명만 표시하며 원문 오류는 제외한다. 작업 시각·재조회 표시만 달라진 경우와 그 밖의 동일한 상태는 매 5분마다 반복 전송하지 않는다. 예상 응답 지연은 작업 주기와 Cloud 스케줄러 상태에 따라 달라진다.

Cloud 터미널에서 네트워크 전송 없이 상태를 확인하려면:

```sh
.venv/bin/python -m gtm --config config.toml --state state/gtm.sqlite status
```

연결 설정을 검토한 뒤 `telegram` 명령을 한 번 실행해 실제 수신과 허용된 개인 계정의 응답을 확인한다. 명령 인자에 토큰을 넣거나 토큰이 포함된 Bot API URL을 로그·스크린샷에 남기지 않는다.

## 중단과 종료

`--repeat`는 실행 횟수 제한이며 정확한 경과 시간이나 Cloud 비용 제한이 아니다. 앱의 `end_at` 검증이 늦게 깨어난 작업의 새 게시·모델 작업을 막는다. Telegram의 2,017회는 168시간 동안의 2,016개 5분 구간과 최종 확인 1회를 위한 설정이며, 실제 종료 알림 시각을 보장하지 않는다.

중단은 Cloud의 기존 worker `pause`/`stop` 명령과 기록해 둔 cron ID의 pause/remove로 처리한다. 이미 외부 서비스에 접수된 예약은 별도로 취소·확인한다. 종료 후 최종 원장·보고서를 보관하고 이 실험의 cron을 제거한다. 인스턴스는 소유자가 Cloud에서 별도로 정지해야 하며 저장 비용은 남을 수 있다. Telegram 상태 명령은 Cloud 결제나 외부 예약을 중단하지 않는다.
