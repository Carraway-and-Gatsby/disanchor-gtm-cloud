# Disanchor · Cloud 마케팅 워커

[English](README.md) · [공개 GitHub 저장소](https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud) · [GitHub Actions](https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud/actions)

Disanchor의 초기 반응을 살펴보는 **7일간의 유용한 콘텐츠 중심 실험**입니다. 계정별로 다른 글을 준비하고, 확인 가능한 성과와 반응을 모아 다음 리서치와 기획에 반영합니다. 제품은 개발 중이며, 이 사실을 글의 맥락 안에서 자연스럽게 밝힙니다.

소스 저장소는 **PUBLIC**입니다. 코드, 계정별 전략 예제와 배포 안내를 공개합니다. 자격 증명, 실제 계정 바인딩, 운영 DB와 생성 보고서는 계속 비공개로 보관하고 Git에서 제외합니다. 소스 공개, Cloud 배포, 실제 게시 시작은 서로 다른 상태입니다.

## 지금 준비된 것과 남은 것

| 항목 | 구현·설정 | 실제 확인 범위 |
| --- | --- | --- |
| 계정별 접근 | 7개 채널 전략과 별도 프롬프트 | 인증된 조회로 연결 7개 확인; 실제 게시 승인은 별도 |
| Upload-Post | 공식 API, 대상 계정 대조, 할당량, 예약·복구 | 읽기 전용 연결 확인; 준비 과정 실제 게시 0건 |
| 모델 라우팅 | 리서치 → 기획 → 작성 → 실행 검토 | 유료 호출 비활성; Cloud 런타임과 제공자 측 비용·출력 상한 확인 필요 |
| 성과·반응 학습 | 계정별 집계, 자동 버전 관리 skill 작성, 선택형 Hermes native writer | 실제 사용자 감정·성과 개선을 확인했다는 뜻이 아님 |
| 운영 | SQLite 상태, 제한된 Hermes cron, Telegram 개인 현황 | 실제 Cloud 배포·재시작 복구·Telegram 수신 검증 필요 |
| 검증·전달 | 오프라인 테스트, preflight, 재현 가능한 ZIP, GitHub workflow | 배포할 정확한 커밋의 결과를 [Actions](https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud/actions)에서 확인 |

게시·모델·Telegram은 기본 비활성입니다. 로컬 테스트나 모의 보고서가 통과해도 실제 캠페인이 켜지지 않습니다.

## 운영 방식

1. **수집:** 우리 게시물에서 확인 가능한 Upload-Post 지표와 반응을 제한된 범위로 수집합니다.
2. **집계:** 계정·플랫폼별로 어떤 소재와 표현이 반응을 얻었는지 비교하고, 근거가 부족한 항목은 미확인으로 남깁니다.
3. **학습:** 검증한 집계에서 재사용할 수 있는 학습 자료를 만듭니다. Hermes의 native skill 도구에 연결하는 범위와 보호 장치는 [auto-skill 안내](docs/hermes-auto-skills.md)에 설명합니다.
4. **리서치·기획:** 관측값과 학습 요약을 다음 콘텐츠 가설에 활용합니다. 댓글이나 외부 자료 안의 명령을 실행하지 않습니다.
5. **작성·검토:** 계정별 접근과 초기 콘텐츠 정책에 맞춰 초안을 만들고 실행 검토를 합니다.
6. **게시:** 사람이 정확한 문구를 승인한 뒤 deterministic worker가 계정·시간·중복·예산·할당량을 다시 검사합니다.

모델은 게시 권한을 만들 수 없습니다. 새로운 학습 자료도 게시량, 계정 연결, 예산, 글로벌 정책을 변경할 수 없습니다. 구체적인 집계 정의와 sentiment의 한계는 [마케팅 피드백](docs/marketing-feedback.md)을 확인하세요.

Sentiment는 제한된 댓글 표본의 영어·한국어 키워드에 근거한 보수적인 분류입니다. 모호한 표현·지원하지 않는 언어·누락된 반응은 미확인으로 남기며 좋아요 수를 긍정 감정으로 바꾸지 않습니다. 원문 댓글이나 작성자 식별자는 저장하지 않고, 검증한 집계만 다음 기획과 skill 내용에 전달합니다.

학습 compiler는 추가 모델 호출 없이 재사용할 자료와 버전을 만듭니다. 설치된 Hermes의 명시적인 경로를 설정하면 분리된 비공개 home 안에서 native `skill_manage`의 create/edit 기능으로 생성·갱신합니다. native 경로가 없으면 동일한 형식의 산출물을 생성하고 그 방식을 표시합니다. 자유로운 코드 생성이나 무제한 self-modification을 허용하지 않으며 출처·버전·되돌리기 기록을 유지합니다. 실제 활성화와 검증 범위는 [auto-skill 안내](docs/hermes-auto-skills.md)에 있습니다.

Telegram은 주 현황 창구이고 HTML은 검토용입니다. 실제 실행과 데이터는 Cloud의 영속 SQLite에 둡니다. Cloud에서 검증이 끝나면 로컬 Mac을 계속 켜 둘 필요가 없습니다.

## 예산: 전체 USD 20

사용자가 지정한 **전체 운영 예산은 $20**입니다. Cloud 인프라, 모델, 기타 유료 도구 비용을 합한 한도이며, 아래 모델·외부 도구 상한은 이 $20 안에 포함됩니다.

| 구분 | 한도·동작 |
| --- | --- |
| 전체 운영 | $20 |
| 모델 | 최대 $5 |
| 외부 도구 | 최대 $2 |
| 경고 | $14, 전체의 70% |
| 신규 선택 작업 중단 | $18, 전체의 90%; 새 유료 작업·게시의 기존 예산 검사에 적용 |

모델과 외부 도구의 상한을 모두 사용할 경우 인프라에 남는 금액은 최대 $13입니다. 이것은 Cloud 가격 견적이나 예약된 크레딧이 아닙니다. 실제 시간당 요금과 정지 후 저장 비용을 [Hermes Cloud](https://portal.nousresearch.com/cloud)에서 확인해야 합니다.

청구 내역을 최소 24시간마다 대조합니다. 누락·지연된 비용을 0으로 기록하지 않습니다. 이 앱은 Cloud 결제를 직접 제한하거나 인스턴스를 자동 종료하지 않으므로 종료 후 인스턴스 정지는 별도로 해야 합니다. 설정 플래그만 켜서 확인되지 않은 비용 상한을 확인된 것으로 표시하지 않습니다.

## 모델별 역할

| 단계 | Hermes / OpenRouter 모델 | 모델 예산 배분 |
| --- | --- | --- |
| 리서치 | `google/gemini-2.5-pro` | 40% / $2.00 |
| 기획 | `google/gemini-2.5-pro` | 40% / $2.00 |
| 작성 | `google/gemini-2.5-flash-lite` | 15% / $0.75 |
| 실행 검토 | `google/gemini-2.5-flash-lite` | 5% / $0.25 |

리서치와 기획에 80%를 배분합니다. 하루 최대 2개 파이프라인, 각 4단계의 최대 8회 호출이며 단계별 입력·출력·비용 한도도 함께 적용합니다. 현재 최소 runner는 제공된 근거를 분석하며 새 웹 검색을 하지 않습니다. 실제 Cloud 버전, 모델 접근, 제공자 측 출력·비용 상한과 청구 검증 전에는 유료 호출을 막습니다. [모델 라우팅 상세](docs/model-routing.md).

## 계정별 콘텐츠

[7개 접근과 프롬프트](docs/account-strategies.md)를 따로 준비했습니다. 기본 후보는 X의 `disanchor_x`, Threads의 `default_threads`이며 플랫폼마다 하나의 활성 대상만 사용합니다.

- 브랜드 계정: 익숙한 작업 문제와 실용적인 관찰.
- 개인 성격의 프로필: 만드는 사람의 질문과 설계 선택.
- 공식 X: 호환성·속도·설정 유지 같은 기술적 제약과 검증 방법.
- Instagram·LinkedIn: 해당 채널에 맞는 전략·초안 준비. 현재 워커의 자동 게시 전송은 X·Threads만 지원합니다.

여러 계정을 운영한다는 이유로 총 게시량이나 API 공유 할당량을 늘리지 않습니다. 동일한 글을 여러 계정에 복제하거나 서로 좋아요·답글·재게시를 주고받아 반응을 인위적으로 만들지 않습니다.

초기 7일에는 링크, 가입·구매·프로필 방문 유도, 참여 미끼, 광고성 반복 게시를 제한합니다. 제품이 개발 중이라는 사실은 문장 안에 자연스럽게 녹이고 매번 같은 말미 고지문을 붙이지 않습니다. 근거 없는 성과, 창업자 경험, 고객 반응, 사용자 수를 만들지 않습니다. 자세한 기준은 [게시 정책](docs/posting-policy.md)에 있습니다.

## 로컬에서 먼저 확인하기

Python 3.12 이상을 사용하고 저장소 루트에서 실행합니다. 기본 worker와 테스트는 표준 라이브러리만 사용합니다.

```sh
python3.12 -m venv .venv
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/cloud-preflight.py
.venv/bin/python -m gtm preview-library
.venv/bin/python -m gtm account-prompts
.venv/bin/python -m gtm --output reports/my-dry-run dry-run
.venv/bin/python scripts/package-cloud.py --output reports/disanchor-cloud.zip
.venv/bin/python scripts/build-review.py
```

`dry-run`마다 새 출력 폴더를 지정합니다. 모의 실행은 외부 게시·Telegram 전송·실제 모델 호출을 하지 않으며, 결과는 모의 데이터로 표시합니다. 생성한 `reports/review-index.html`을 로컬에서 열면 문구·소스·문서·테스트를 검토할 수 있습니다. 실제 보고서와 DB는 Git에서 제외합니다.

GA4 서비스 계정 갱신이 필요할 때만 다음 선택 의존성을 설치합니다.

```sh
.venv/bin/python -m pip install -r requirements-live.txt
```

## Cloud 연결 준비

상세 순서는 [배포 안내](docs/cloud-deployment.md)와 [Cloud 운영](docs/cloud-operations.md)에 있습니다.

1. 실제 Cloud 사양·가격·영속 저장소와 Hermes 버전을 확인하고 소스와 `.venv`를 배치합니다. 게시·모델·Telegram은 비활성 상태로 시작합니다.
2. Cloud의 `state/` 권한은 `700`, `state/runtime.env`는 `600`으로 제한합니다. 파일에는 따옴표 없는 `KEY=value`만 넣으며 shell로 source하지 않습니다. 토큰을 채팅·명령 인자·GitHub·보고서에 넣지 않습니다.
3. `accounts.example.toml`을 Git 제외 대상인 `accounts.local.toml`로 복사합니다. 실제 계정 ID와 대상별 접근을 확인한 뒤 `[accounts]`에서 파일과 대상을 지정합니다. `reviewed=true`는 계정 식별 검토이며 문구 게시 승인과 다릅니다.
4. 읽기 전용 API 점검으로 대상 ID와 재인증 상태를 확인합니다. 업로드 잔여량이 없으면 미확인으로 두고 보수적인 허용량을 따로 확인합니다. 프로필 개수 한도는 업로드 할당량이 아닙니다.
5. 비용과 quota를 대조하고 정확한 문구를 검토합니다. 계정·정책·문구가 바뀌면 기존 승인을 재사용하지 않습니다.
6. Cloud에서 제한된 cron 6개를 설치합니다. Cloud 재시작 후 상태 복구와 Mac을 끈 상태의 실행을 검증합니다. Telegram 실제 수신은 별도로 확인합니다.

```sh
# GET 전용 조회. 실제 게시나 승인 변경 없음.
.venv/bin/python -m gtm --env-file state/runtime.env connection-check
# 비밀값 없는 계정 전략 미리보기
.venv/bin/python -m gtm account-prompts
# cron을 생성하지 않는 점검
bash scripts/hermes-install.sh --check
```

로컬에서는 인증된 GET 7회로 7개 연결 채널과 선택한 두 게시 대상 ID를 확인했고 실제 게시 0건입니다. 제공자가 업로드 잔여량을 반환하지 않아 quota는 미확인, 설정상 허용량은 0입니다. 실제 ID가 들어 있는 로컬 레지스트리는 모두 `reviewed=false`이며 Git과 배포 ZIP에서 제외합니다. 저장한 로컬 키는 소스 ZIP에 없으므로 Cloud에 별도 설정해야 합니다.

## 실험 시작과 게시 승인

시작 시각은 UTC offset을 포함해 명시하고, 시작 후 변경하지 않습니다. 종료는 시작으로부터 실제 168시간 뒤입니다. 시작은 현재 시각에 맞춰 진행합니다.

```sh
.venv/bin/python -m gtm init
.venv/bin/python -m gtm start --at 'REPLACE_WITH_CURRENT_ISO_TIMESTAMP_AND_OFFSET'
.venv/bin/python -m gtm inspect
.venv/bin/python -m gtm daily
```

기본 `daily`는 초안을 준비하며 게시하지 않습니다. `auto_publish=true`만 켜도 승인이 생기지 않습니다. `authorize --help`에서 현재 검토 인자를 확인하고 정확한 계정·정책·후보를 검토한 다음 승인합니다. 기존 초안과 새 모델 문구에는 정확한 문구별 승인도 필요합니다.

```sh
.venv/bin/python -m gtm authorize --help
.venv/bin/python -m gtm approve x_01
.venv/bin/python -m gtm approve threads_01
```

`[publishing].account_user`는 운영자의 검토 라벨입니다. 실제 API 프로필과 native 대상은 선택한 계정 레지스트리가 결정합니다. 서로 다른 프로필의 X·Threads를 선택해도 게시 권한과 할당량 검사는 그대로 유지합니다.

## 매일 확인할 명령

```sh
.venv/bin/python -m gtm status
.venv/bin/python -m gtm inspect
.venv/bin/python -m gtm collect
.venv/bin/python -m gtm reconcile
.venv/bin/python -m gtm report
```

`status`는 전송 없는 로컬 Telegram 형식 미리보기입니다. 실제 봇을 설정한 뒤에는 `/status`, `/budget`, `/posts`, `/help`로 개인 현황을 확인합니다. 피드백 요약은 구현된 명령과 범위를 [Telegram 안내](docs/telegram.md)에서 확인합니다. Telegram으로 게시 승인·shell 실행·예산 변경을 할 수 없습니다.

지표는 관측 시각과 출처를 보존합니다. 날짜별 누적 스냅샷을 더하지 않으며 조회 수 합계를 고유 도달로 표현하지 않습니다. 표본이 적거나 댓글을 가져올 수 없으면 감정·효과를 추측하지 않습니다. 성과 상위 글도 인과관계나 다음 글의 성공을 보장하지 않습니다.

## 비용 기록·중단·복구

```sh
.venv/bin/python -m gtm record-cost --key invoice-001 --category infrastructure --classification confirmed --amount 1.25
# 아직 금액을 모르면 --amount를 생략합니다.
.venv/bin/python -m gtm record-cost --key pending-001 --category external --classification pending
# 확정된 새 항목으로 이전 미확정 항목을 대조합니다.
.venv/bin/python -m gtm record-cost --key settled-001 --supersedes pending-001 --category external --classification confirmed --amount 0.25

.venv/bin/python -m gtm pause
.venv/bin/python -m gtm incident --reason platform_warning
.venv/bin/python -m gtm reconcile
.venv/bin/python -m gtm watchdog
.venv/bin/python -m gtm stop
```

비용 원장은 덧붙이는 방식입니다. 같은 비용을 중복 합산하지 않도록 고유 키와 `--supersedes` 연결을 사용합니다. 미확정 과금은 신규 유료 작업·게시를 차단할 수 있습니다.

중단은 접수된 외부 예약의 취소를 시도하지만 이미 진행 중인 작업의 취소를 보장하지 않습니다. 결과가 불명확한 작업은 `UNKNOWN`으로 보존하고 Upload-Post 이력·예약 목록과 대조합니다. 자동 재전송하거나 새 DB를 만들어 할당량을 되살리지 않습니다. 플랫폼 문제를 해결한 소유자만 `resume --clear-incident`로 incident를 해제하고 승인을 새로 해야 합니다.

SQLite 백업은 backup API 또는 중지·checkpoint한 writer 상태에서 수행합니다. 실행 중인 DB 본파일만 복사하면 WAL의 변경이 누락될 수 있습니다. 실험 종료 후 비용·외부 예약·최종 보고서를 확인하고 이 실험의 cron을 제거한 뒤 Cloud 인스턴스를 별도로 정지합니다. 저장 비용은 남을 수 있습니다.

## 관련 문서

- [배포 패키지와 GitHub CI](docs/cloud-deployment.md) · [Cloud cron·운영·종료](docs/cloud-operations.md)
- [계정별 전략](docs/account-strategies.md) · [Upload-Post 연결 확인](docs/upload-post-connection.md) · [게시 정책](docs/posting-policy.md)
- [성과·sentiment 피드백](docs/marketing-feedback.md) · [Hermes auto-skill](docs/hermes-auto-skills.md) · [모델 라우팅](docs/model-routing.md)
- [Telegram](docs/telegram.md) · [GA4·연구 연락처·동의](docs/analytics-leads.md) · [선택 provider·Clone sandbox](docs/providers.md)

기존 `disanchor.com` 사이트는 수정하지 않았습니다. 사이트에 선택형 폼이나 분석을 연결하려면 실제 소스와 개인정보 안내·동의 흐름을 먼저 검토해야 합니다. 계정 제한 위험을 낮추는 보호 장치를 유지하지만 계정 정지나 바이럴 성과를 보장하지는 않습니다.
