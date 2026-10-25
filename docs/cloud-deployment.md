# Cloud 배포 준비와 소스 패키지

현재는 [게시 잠금 staging 보안 절차](cloud-staging-security.md)를 먼저 적용한다. 초기 Cloud에는 API 키를 복사하지 않으며, 설치 wrapper의 `--no-publish` 잠금을 유지한다. 아래의 실제 계정 연결·실험 시작은 향후 별도 승인 단계다.

[English overview](../README.md) · [한국어 운영 안내](../README.ko.md) · [Public GitHub repository](https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud) · [GitHub Actions](https://github.com/Carraway-and-Gatsby/disanchor-gtm-cloud/actions)

배포물은 Python 3.12+에서 실행하는 소스 ZIP이다. Docker나 UI 서버는 필요하지 않다. GitHub CI와 로컬 preflight는 코드·기본 설정·패키지만 검증하며, Upload-Post 인증 성공이나 Hermes Cloud 배포 완료를 뜻하지 않는다. 실제 토큰은 저장소·ZIP·CI에 넣지 않는다.

소스 저장소는 공개다. 자격 증명, 실제 계정 바인딩, 운영 DB와 생성 보고서는 계속 비공개로 보관하고 Git에서 제외한다. 소스 공개와 Actions 성공, Cloud 배포, 실제 게시 시작은 각각 확인해야 한다. 새 커밋은 별도 실행을 만들므로 배포할 정확한 커밋의 결과를 위 Actions 목록에서 확인한다.

전체 Cloud 운영 예산은 **USD 20**이며 인프라·모델·기타 유료 도구를 합산한다. 모델 $5와 외부 도구 $2는 전체 한도 안의 상한이다. $14에서 경고하고 $18에서 신규 선택 유료 작업·게시를 기존 예산 검사로 막는다. 실제 Cloud 시간당 요금과 정지 후 저장 비용을 먼저 확인한다. 앱은 인스턴스를 자동 종료하거나 Cloud 제공자의 청구 상한을 설정하지 않는다.

소유자가 승인한 Upload-Post 키는 로컬의 비공개 `state/runtime.env`에 저장됐다. Cloud용 소스 패키지에는 들어가지 않으므로 원격 인스턴스에 별도로 안전하게 설정해야 한다. 키 보관, 인증된 조회, 실제 대상 검토, 게시 승인은 각각 구분한다.

로컬 GET 전용 점검은 인증과 7개 연결 채널을 확인했고, 선택한 `disanchor_x`의 X와 `default_threads`의 Threads 대상 ID 비교도 통과했다. 해당 점검은 GET 7회·게시 0건이다. 실제 ID를 담은 비공개 `accounts.local.toml`의 검토 상태는 모두 `false`로 남겨 두었다. 제공자가 업로드 잔여량을 반환하지 않아 quota는 미확인 상태이며, Cloud 연결과 게시 승인은 여전히 별도다. 이 작업 공간에서 같은 점검을 다시 할 때는 다음 비공개 점검 설정을 명시한다. 이 파일은 패키지에 포함되지 않는다.

```sh
.venv/bin/python -m gtm --config state/connection-check.toml --env-file state/runtime.env connection-check
```

## 로컬에서 검증하고 묶기

```sh
python3.12 scripts/cloud-preflight.py
python3.12 -m unittest discover -s tests -v
bash -n scripts/hermes-install.sh
python3.12 scripts/build-review.py
python3.12 scripts/package-cloud.py --output reports/disanchor-cloud.zip
```

`cloud-preflight.py`는 기본적으로 네트워크·subprocess·파일 변경 없이 소스 목록, Python 버전, 설정 형식, 비활성 기본값, 로컬 비밀 파일의 권한만 검사한다. `state/runtime.env`가 있다면 내용은 읽지 않고 작업자 소유의 일반 파일과 비공개 권한인지 확인한다. 파일이 없다는 결과는 인증 실패가 아니라 아직 확인하지 않았다는 뜻이다.

`--runtime-check`를 붙이면 해당 호스트에서 `hermes --version`과 `hermes cron create --help`만 실행한다. 모델 호출·스케줄 생성은 하지 않으며, 로컬 Mac에서 통과해도 Cloud의 런타임은 별도로 검사해야 한다. 새 모델 라우팅의 실제 지원과 provider 측 출력·비용 상한은 이 검사로 확인되지 않는다.

`build-review.py`는 현재 시간표에 해당하는 seed를 정책 검사한 뒤 게시 후보와 7개 계정 전략을 HTML로 만들고, 허용 목록의 모든 소스·문서·테스트에 직접 링크한 검토 인덱스를 생성한다. 북미 주간 설정은 6개 슬롯, 이전 legacy 설정은 10개 슬롯이다. 체크인된 `accounts.example.toml`만 사용하며 비밀 파일·로컬 바인딩·운영 DB는 읽지 않는다. ZIP을 다시 만들지는 않고, 기존 ZIP의 manifest가 현재 소스와 다른 경우 갱신 필요 상태를 표시한다. 최종 ZIP 생성 후 한 번 더 실행하면 인덱스의 일치 상태도 갱신된다.

패키지는 명시적으로 허용한 소스·설정·문서·테스트·빈 `.env.example`만 포함한다. `state/`, `reports/`, 실제 `.env`, 계정용 로컬 `accounts.toml`·`accounts.local.toml`, 캐시, 저장소 메타데이터, 비밀 파일은 제외한다. `accounts.example.toml`과 계정별 프롬프트는 포함하며 실제 native 계정 연결과 권한 검증은 Cloud에서 별도로 한다. symlink, 빈 값이 아닌 환경 예제, 식별 가능한 자격 증명 패턴이 있으면 생성을 중단한다. 이 검사는 임의의 비밀값을 완벽하게 탐지하는 보장은 아니므로 소스 검토도 필요하다. 동일한 소스는 시간·파일 수정 시각에 관계없이 같은 ZIP과 SHA-256을 만든다. `bundle-manifest.json`에는 모든 소스의 크기와 SHA-256이 들어 있다.

## Cloud에서 실행 준비

1. [Hermes Cloud](https://portal.nousresearch.com/cloud)의 인스턴스 사양·현재 요금·영속 저장소를 확인한다. 인프라와 모델·기타 도구의 예상 총비용이 $20 안에 들어가는지 검토한다. ZIP 또는 검토한 공개 저장소 커밋을 해당 저장소에 배치한다. 실제 배포 경로나 자격 증명은 GitHub에 커밋하지 않는다.
2. 압축을 풀고 `python3.12 -m venv .venv`를 실행한다. 핵심 worker와 테스트는 표준 라이브러리만 사용한다. GA4 서비스 계정 갱신이 필요할 때만 `.venv/bin/python -m pip install -r requirements-live.txt`로 선택 의존성을 설치한다.
3. 설정을 변경하기 전에 `.venv/bin/python scripts/cloud-preflight.py --runtime-check`와 테스트를 실행한다. preflight는 배포 기본값의 안전성 검사이므로 실제 활성화 설정으로 바꾼 뒤에는 의도적으로 실패한다.
4. `state/`를 비공개로 만들고 필요한 자격 증명을 `state/runtime.env`에 저장한다. 권한은 디렉터리 `700`, 파일 `600`으로 제한한다. `.venv/bin/python -m gtm --env-file state/runtime.env connection-check`로 GET 전용 [Upload-Post 연결 점검](upload-post-connection.md)을 실행한다. 기본 CLI는 비밀 파일을 자동으로 읽지 않는다. 프로필 이름·실제 계정 ID·재인증 상태·업로드 할당량을 각각 확인한다.
5. `accounts.example.toml`을 무시 대상인 `accounts.local.toml`로 복사한다. 조회한 실제 ID와 사람이 확인한 검토 상태를 넣고 `[accounts].registry_file`을 로컬 파일로 설정한다. 현재 선택은 X의 `disanchor_x`, Threads의 `default_threads`이며, 활성 대상은 플랫폼당 하나다. [7개 계정 전략](account-strategies.md)은 서로 다른 문체와 소재를 위한 것이고 게시량·예산을 늘리는 설정이 아니다. Instagram과 LinkedIn은 전략·초안 준비만 지원한다. `.venv/bin/python -m gtm account-prompts`는 비밀값 없이 전략을 검토한다.
6. [Cloud 운영 안내](cloud-operations.md)에 따라 실험을 초기화하고, 정확한 시작 시각을 기록하고, 6개 제한된 cron을 설치한다. 설치기는 기본적으로 `--check`이며, Cloud 확인과 정확한 런타임 버전 일치가 있어야 `--install`을 진행한다. 설치 중 실패하면 반환된 작업 ID를 확인하고 중복 없이 정리한다. 모델은 원격 런타임 지원과 provider 측 비용·출력 상한을 별도로 확인하기 전까지 비활성으로 둔다.
7. [Telegram 설정](telegram.md)을 검토한 뒤 전용 봇과 허용된 개인 운영자 계정을 연결한다. `/status`, `/budget`, `/posts`, `/help`로 읽기 전용 현황을 확인한다. 일일 요약은 고정 한국 시간(KST) 기준이며 기본 09시 이후 첫 정상 poll에서 전달된다.
8. Cloud 재시작 후 SQLite 상태가 유지되고, Mac을 꺼도 예약 작업·Telegram 조회가 동작하는지 실제로 검증한다. 이 확인 전에는 무인 운영이 완료됐다고 표시하지 않는다.

성과 집계와 학습은 [마케팅 피드백](marketing-feedback.md), [Hermes auto-skill](hermes-auto-skills.md)의 범위로 운영한다. 조회 권한이 없는 댓글을 긁어오거나, 여러 계정의 반응을 한데 섞어 감정을 추정하지 않는다. 새로운 학습 자료가 게시 승인·예산·기존 보호 장치를 변경할 수 없다. 실제 반응이 없는 배포 전 상태는 데이터 부족으로 표시한다.

실제 게시 전에는 개발 중이라는 맥락을 문장에 자연스럽게 연결한 현재 후보 문구와 대상 계정을 다시 승인한다. 고정 고지문을 붙인 이전 후보의 승인은 새 문구에 이전되지 않는다. 초기 7일의 링크·광고·참여 유도 제한과 총 10건 한도는 모든 계정 전략에 적용한다.

`[publishing].account_user`는 `authorize --account-user`에서 대조하는 운영자의 검토 라벨이다. 실제 전송 프로필·native 대상은 선택한 계정 레지스트리에서 정하므로 두 플랫폼이 같은 API 프로필을 써야 한다는 뜻이 아니다.

GitHub workflow는 Python 3.12에서 offline preflight, shell 문법, 전체 unittest, 소스 패키징을 실행한다. provider 비밀값이나 실제 게시 호출은 사용하지 않는다. 현재 [checkout](https://github.com/actions/checkout)·[setup-python](https://github.com/actions/setup-python) 공식 사용법에 맞춘 v7 action과 저장소 읽기 권한만 사용한다. GitHub에서 실제 workflow가 성공했는지는 별도 실행 결과로 확인한다.

Cloud 사용료는 실험 종료나 cron 횟수 소진으로 자동 중단되지 않는다. 최종 비용·외부 예약·보고서를 확인한 뒤 소유자가 인스턴스를 정지한다. 저장 요금은 남을 수 있다.
