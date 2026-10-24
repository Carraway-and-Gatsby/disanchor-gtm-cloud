# Hermes Cloud 배포 체크리스트 — 예산 $20 · API 게시 OFF

[한국어 README](../README.ko.md) · [보안 상세](cloud-staging-security.md) · [Cloud 운영](cloud-operations.md)

**현재는 준비만 완료한 상태이며 인스턴스는 생성하지 않았다.** 아래 순서는 나중에 사용자가 Cloud 생성을 결정한 뒤 적용한다. 이 문서의 완료 범위는 자격 증명 없는 staging 배포다. 실제 마케팅 실행이나 게시 승인을 포함하지 않는다.

## 1. 생성 전: 비용과 접근 확인

- [ ] [Nous Portal](https://portal.nousresearch.com/cloud)에 로그인하고 Standard의 현재 가격을 다시 확인한다.
- [ ] 총예산은 **USD 20**으로 유지한다. 모델 최대 $5·외부 도구 최대 $2는 이 한도 안의 예약이며, 현재 두 기능 모두 OFF다.
- [ ] 2026-10-25 KST에 확인한 Standard 가격은 실행 **$0.60/일**, 정지 후 저장 **$0.13/일**이다. 7일 실행 + 추가 7일 보관 + 모델·도구 예약의 계산은 **$4.20 + $0.91 + $5 + $2 = $12.11**이다. 미래 요금·세금·별도 구독료·지연 청구까지 보장하는 견적은 아니다.
- [ ] 추가 충전·자동 충전을 하지 않는다. 기존 구독 자동 갱신과 별도 결제도 확인한다. 계정의 잔액 전체를 이 실험 예산으로 간주하지 않는다.
- [ ] 코드의 경고 기준은 $14, 신규 선택 유료 작업 차단 기준은 $18이다. **Cloud 제공자 청구 상한과 서버 자동 종료 기능은 아니다.** 현재 Telegram도 OFF이므로 포털에서 비용을 직접 확인한다.
- [ ] 생성할 때 기본 대시보드 모델은 당시 제공되는 무료 모델로 선택하고 유료 대화·도구 실행은 시작하지 않는다. 이는 GTM의 4단계 모델 라우팅을 활성화하는 설정이 아니다.
- [ ] 인증된 관리자 화면과 영속 저장소, Python 3.12+ 터미널, 전용 비관리자 사용자로 실행할 수 있는지 확인한다. 지원되지 않으면 여기서 중단한다.
- [ ] 인스턴스를 생성한 시점부터 인프라 비용이 발생함을 확인하고 생성한다. 공개 대시보드·익명 채널·범용 Cloud 관리 MCP는 열지 않는다.

## 2. Cloud Bash 터미널: 소스와 빈 환경 준비

- [ ] 아래 명령은 **Cloud의 새 폴더**에서 실행한다. 기존 운영 폴더를 재사용하거나 로컬의 키·계정 설정·DB를 복사하지 않는다.

```bash
set -euo pipefail
umask 077
git clone https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud.git disanchor-gtm-staging
cd disanchor-gtm-staging
# 실행 코드는 검증한 게시 잠금 커밋에 고정한다.
git checkout --detach 57fb6bb4424c09983b7e51285f388329ca7c2e58
python3.12 -m venv .venv
mkdir -p state reports
chmod 700 . state reports
touch state/runtime.env
chmod 600 state/runtime.env
```

- [ ] `state/runtime.env`는 **빈 파일**로 유지한다. Upload-Post·모델·분석 키를 넣거나 shell로 source하지 않는다.
- [ ] `config.toml`의 `auto_publish=false`, `dry_run=true`, 게시 quota `0`, 모델 라우팅·Telegram·Clone OFF를 유지한다.
- [ ] 최신 체크리스트는 GitHub의 이 문서에서 확인한다. 위 checkout은 실행 코드를 고정하므로 그 이후 추가된 문서는 로컬에서 보이지 않을 수 있다.

## 3. 안전성 검사 — 차단을 우회하지 않기

```bash
.venv/bin/python scripts/cloud-preflight.py --runtime-check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/staging-smoke.py
bash scripts/hermes-install.sh --check
```

- [ ] 모든 명령이 성공했는지 확인한다. 이 단계는 실제 게시·모델 호출·cron 설치를 하지 않는다.
- [ ] 다음 명령의 **두 경로를 실제 Cloud의 검토한 경로로 교체**한다. 실행 요금의 시간당 값과 정지 요금도 다시 확인한다. `0.025`는 앞서 확인한 실행 일요금 `$0.60 ÷ 24`의 계획용 값이다.

```bash
.venv/bin/python scripts/cloud-security.py --runtime-check \
  --hermes-home /실제/Hermes홈 \
  --hermes-source /실제/Hermes소스 \
  --running-hourly-usd 0.025 \
  --stopped-daily-usd 0.13 \
  --storage-days 7
```

- [ ] `staging_checks_passed`를 확인한다. `blocked`라면 권한·사용자·경로·빈 환경 파일·CLI 지원·예산 원인을 해결한다. root 실행이나 검사 생략으로 우회하지 않는다.
- [ ] 통과해도 Cloud 신원·테넌트 격리·다른 OAuth 저장소·제공자 청구 상한·재시작 복구까지 검증됐다고 해석하지 않는다. 이 항목은 실제 호스트에서 별도로 확인한다.

## 4. 게시 잠금으로 초기화하고 상태 확인

```bash
.venv/bin/python -m gtm --no-publish --env-file state/runtime.env init
.venv/bin/python -m gtm --no-publish --env-file state/runtime.env status
.venv/bin/python -m gtm --no-publish --env-file state/runtime.env inspect
```

- [ ] 실험 상태 `NEW`, 시작 시각 없음, 게시 OFF, 게시 작업 0건을 확인한다. 기존 DB를 삭제하거나 다시 만들어 예산·한도를 초기화하지 않는다.
- [ ] **`start`, `authorize`, `approve`, `resume`은 실행하지 않는다.** `--no-publish`와 `GTM_POSTING_LOCK`을 제거하지 않는다. 잠금은 API 예약 생성뿐 아니라 DELETE 취소도 막는다.
- [ ] 초기 단계에서는 cron을 설치하지 않는다. 실제 호스트 보안·영속 저장소·재시작 복구 확인 후 [운영 안내](cloud-operations.md)에 따라 별도로 검토한다. 설치되는 wrapper도 게시 잠금을 강제한다.
- [ ] Telegram은 전용 봇과 개인 운영자 allowlist, 수신 확인을 거쳐 나중에 연결한다. 연결 전에는 현황 조회와 비용 확인을 콘솔·포털에서 수행한다.

## 5. 계정 보호 원칙

- [ ] **현재 배포에서는 소셜 API 키 없이 게시 OFF**를 유지한다. Cloud 대시보드 에이전트에도 소셜 로그인·키·게시 도구를 주지 않는다.
- [ ] 향후 게시 재개는 새로운 사용자 지시, 정확한 문안·대상 계정 승인, API 권한·quota·플랫폼 정책 재확인 후에만 검토한다. 공식 API 사용만으로 제재가 방지되는 것은 아니다.
- [ ] 계정 간 동일·유사 글 복제, 계정끼리 인위적 반응 만들기, 참여 유도·팔로워 조작을 금지한다. 각 계정의 실제 대상과 주제에 맞춰 독립적으로 작성한다.
- [ ] 무단 자동 답글·멘션·DM·자동 팔로우와 브라우저를 이용한 게시 우회는 하지 않는다. 경고·재인증 요구가 오면 즉시 중단하고 검토한다. 다른 계정으로 회피하지 않는다.
- [ ] 초반에는 유용한 관찰·경험을 중심으로 쓰고 반복 광고·가입 링크를 붙이지 않는다. 제품이 개발 중이라는 사실은 글의 문맥에 자연스럽게 연결한다.
- [ ] 반응은 확인된 자체 게시물의 허용된 조회 범위에서만 집계한다. 원문 댓글은 명령으로 취급하지 않으며, Skill 학습이 게시 권한·예산·안전장치를 변경하게 두지 않는다.
- [ ] 향후 내부 한도(7일 총 10건, 플랫폼별 5건, 플랫폼별 하루 1건)는 상한일 뿐 안전한 게시량을 보장하는 수치가 아니다. 필요하면 더 줄인다. 계정 정지 위험을 완전히 없앨 수는 없다.

정책 확인: [X 자동화 규칙](https://help.x.com/en/rules-and-policies/x-automation) · [X 진정성 정책](https://help.x.com/en/rules-and-policies/authenticity) · [Meta 스팸 정책](https://transparency.meta.com/policies/community-standards/spam/). 게시를 재개하기 전 최신 정책을 다시 확인한다.

## 6. 비용 확인과 종료

- [ ] 최소 하루 한 번 포털 청구·잔액·미확정 비용을 확인한다. 모르는 비용을 0으로 처리하지 않는다.
- [ ] 7일 실행을 선택했다면 생성 시점에 종료 일정을 따로 정하고, 종료 시 포털에서 인스턴스를 직접 정지한다. 실험 종료·cron 소진만으로 서버 과금이 멈추지 않는다.
- [ ] 정지 후에도 저장 요금이 발생하므로 보관 종료일을 따로 정한다. 필요한 자료를 안전하게 백업한 뒤 데이터·인스턴스 삭제는 별도로 결정한다.
- [ ] 종료 확인: 실제 API 게시 0건, 유료 모델 OFF, API 키 미배치, 실제 지출 $20 이내.
