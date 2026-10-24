# Hermes Cloud: 게시 잠금 상태의 배포 준비 / Locked staging

[한국어 운영 안내](../README.ko.md) · [English overview](../README.md) · [Deployment](cloud-deployment.md)

현재 단계는 **새 Cloud 인스턴스에 올릴 소스 준비**다. 실제 API 게시를 허용하지 않는다. 로컬 검사나 CI 통과를 Cloud 배포·호스트 보안 검증으로 표시하지 않는다.

`python3.12 scripts/staging-smoke.py`는 배포 ZIP을 임시 비공개 디렉터리에 풀고, 빈 환경 파일·네트워크 소켓 차단 상태에서 초기화·상태 조회·비활성 Telegram 명령을 검증한다. 실험 시작은 거부되고 DB는 `NEW`, 게시 작업은 0건이어야 한다. 이 검사는 GitHub Actions에서도 실행한다.

## 게시 OFF를 유지하는 경계

- 모든 설치 wrapper가 `--no-publish`와 `GTM_POSTING_LOCK=1`을 강제한다. 설정 파일이 실수로 바뀌어도 worker와 Upload-Post 전송 계층에서 GET 외 요청을 거부한다. 예약 생성뿐 아니라 DELETE 취소도 차단하므로, 기존 외부 예약이 있다면 운영자가 제공자 화면에서 별도로 확인한다.
- `--no-publish` CLI는 실험 시작·게시 승인·재개 명령도 거부한다. 리서치·기획·작성·학습은 기존 검증을 유지하며, 모의 실행은 실제 API와 연결하지 않는다.
- 초기 Cloud에는 Upload-Post 키, 실제 계정 바인딩, 운영 DB, 모델 키를 전송하지 않는다. `state/runtime.env`는 빈 비공개 파일로 만든다. 게시가 비활성인 상태에서도 키를 가진 동일 사용자 프로세스가 그 파일을 읽을 수 있으므로, 키를 두지 않는 것이 초기 배포의 핵심 경계다.
- 프로세스 잠금은 OS 샌드박스가 아니다. 임의 코드 실행이나 호스트 관리자 권한까지 방어한다고 주장하지 않는다. 모델에 shell·게시 도구·Cloud 관리 MCP를 주지 않고, 게시 자격 증명을 Hermes 홈·소스 `.env`에 섞지 않는다.

## USD 20 예산

총 **$20** 안에서 모델 최대 $5, 외부 도구 최대 $2를 예약한다. $14에 경고하고 $18에서 신규 선택 유료 작업을 막으며 나머지 $2는 지연 청구 등을 위한 여유로 둔다. 168시간 실행 및 정지 후 보관 기간의 인프라·저장 비용은 합계 $11 이하인 요금제를 골라야 이 보수적 계획에 맞는다. 앱의 $18 검사는 Cloud 제공자 청구를 강제로 제한하거나 인스턴스를 종료하지 않는다.

실행 요금과 정지 후 저장 요금은 포털의 실제 견적을 확인한다. 입력하지 않으면 `cloud-security.py`는 무료라고 가정하지 않고 차단한다. 아래 값은 설명용 예이며 현재 가격이 아니다.

```sh
.venv/bin/python scripts/cloud-security.py --runtime-check \
  --hermes-home "$HERMES_HOME" --hermes-source /reviewed/hermes/source \
  --running-hourly-usd 0.05 --stopped-daily-usd 0.05 --storage-days 7
```

예시 합계는 $15.75다: `168×0.05 + 7×0.05 + 5 + 2`. 자동 충전은 끄고, 별도 모델 키는 재설정 없는 $5 한도·만료일을 실제 제공자에서 검증하기 전에는 유료 모델을 켜지 않는다. 7일 후 인스턴스 정지와 저장 데이터의 보관 종료를 별도로 수행해야 한다. 포털에 강제 청구 상한이 없다면 앱 설정만으로 $20 초과를 완전히 방지한다고 주장하지 않는다.

## 새 Cloud 호스트에서의 순서

1. 로그인된 Nous Portal에서 현재 인스턴스 요금, 정지 후 저장 요금, 잔액·자동 충전, 대시보드의 인증 경계를 검토한다. 공개 접근·익명 채널·범용 Cloud 관리 도구는 활성화하지 않는다. 소스 ZIP에는 자격 증명이 없다.
2. 검토한 커밋/ZIP을 **전용 영속 디렉터리**에 배치하고 전용 비관리자 사용자로 실행한다. 워크스페이스·`state`·`reports`는 `700`, 파일·DB·자격 증명은 `600`으로 둔다. 기존 인스턴스 파일을 덮어쓰지 않는다.
3. Python 3.12+ venv를 만들고 기본 설정 그대로 테스트한다. 핵심 worker는 외부 패키지가 필요 없다. `config.toml`의 게시·모델·Telegram·Clone은 OFF, 게시 quota는 0으로 유지한다. 처음에는 실험을 시작하거나 cron을 설치하지 않는다.
4. 새 디렉터리에서만 빈 `state/runtime.env`를 만든다. 기존 파일이 있다면 덮어쓰지 않고 먼저 내용을 안전하게 검토한다. 키를 명령 인자·shell history·로그에 넣지 않는다.
5. 아래의 읽기 전용 검사와 초기화를 실행한다. `cloud-security.py`는 Linux·비관리자 실행·비공개 디렉터리/파일·빈 staging 환경 파일·현재 프로세스의 키 부재·명시한 Hermes 홈/소스의 `.env` 키 이름을 검사한다. 키 값은 출력하지 않는다. `.env` 이외의 OAuth 저장소, OS 격리, 포털 인증, 청구 상한, 재시작 복구는 별도 검증 항목으로 남는다. 실제 요금·Hermes 경로·런타임 검사가 빠지면 성공으로 표시하지 않는다.

```sh
.venv/bin/python scripts/cloud-preflight.py --runtime-check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/cloud-security.py --runtime-check
# 위 security 명령은 실제 견적과 명시적 Hermes 경로가 없으므로 blocked가 정상이다.
.venv/bin/python -m gtm --no-publish --env-file state/runtime.env init
.venv/bin/python -m gtm --no-publish --env-file state/runtime.env status
bash scripts/hermes-install.sh --check
```

6. Hermes Cloud 버전과 CLI 옵션, 영속 디스크·재시작 후 동일 DB 보존, 실제 청구 방식을 점검한 뒤에만 cron 설치를 검토한다. 설치해도 wrapper의 게시 잠금은 유지한다. 최초 `daily`는 실험 시작 전에는 차단되는 것이 맞다.
7. Telegram 토큰·개인 chat/user ID는 별도 비공개 파일에서 연결하며, 운영자가 요청한 전용 봇으로만 상태를 전달한다. owner allowlist·그룹 거부·수신 확인을 거치기 전에는 연결 완료로 표시하지 않는다. 초기 credential-free 검사와 Telegram 연결 검사는 다른 단계다.

실제 모델 라우팅과 유료 실행은 Cloud의 정확한 Hermes 버전, provider 출력/지출 상한 검증이 아직 필요한 별도 단계다. 게시 재개는 향후 명시적인 사용자 지시와 정확한 문안/계정 승인 없이는 하지 않는다.

## English

Prepare a fresh, credential-free staging deployment with a **USD 20 total** envelope. All installed wrappers force `--no-publish` and `GTM_POSTING_LOCK=1`. The worker and real Upload-Post transport reject non-GET requests even if publishing is enabled accidentally in configuration. The staging CLI also rejects start, authorize, approve and resume. This is an application guard, not an OS sandbox.

Reserve at most $5 for models and $2 for external tools. Keep the planned 168-hour infrastructure plus stopped-storage costs within $11, below the $18 optional-work stop point; retain $2 for delayed billing. Unknown prices block readiness. Provider-enforced limits, no automatic top-up, current quotes, instance shutdown and storage retention must be verified separately. The application cannot enforce the hosting provider's bill.

Run `scripts/cloud-security.py` on the actual Linux host as a dedicated non-root worker. It checks private filesystem permissions, an empty staging env file, absence of project credentials in the process, explicitly supplied Hermes `.env` locations, cron capability and supplied price arithmetic. It makes no network requests and prints no credential values. Missing runtime, profile or pricing checks remain blocked. A local pass never attests Cloud identity, tenant isolation, restart recovery, provider billing controls, model hard caps or Telegram delivery.

Do not transfer the local Upload-Post key or operational DB into initial staging. Keep all live features off and do not start the experiment. Verify the host, persistent state and owner-only Telegram delivery before considering any limited paid inference. Social publishing remains locked until a future explicit user instruction and all existing content/account approvals.

Official references checked for preparation: [Cloud billing and stopped storage](https://portal.nousresearch.com/cloud), [script-only cron](https://hermes-agent.nousresearch.com/docs/guides/cron-script-only), [provider configuration](https://hermes-agent.nousresearch.com/docs/integrations/providers), [Cloud MCP privileges](https://hermes-agent.nousresearch.com/docs/guides/manage-hermes-cloud-with-mcp).
