# Hermes automatic skill learning / Hermes 자동 스킬 학습

The feedback loop creates and revises a reusable Hermes skill as new validated audience evidence arrives. Research and planning receive its small, checked lessons; publishing authority stays in the existing approval and budget controls.

검증된 반응 데이터가 바뀔 때마다 Hermes가 재사용할 스킬을 자동 생성·갱신합니다. 리서치와 기획에는 검증된 요약만 전달하며, 게시 승인과 예산 안전장치는 그대로 유지합니다.

## What actually runs / 실제 실행 방식

`gtm/learning.py` compiles aggregate measurements into `disanchor-marketing-feedback/SKILL.md`. With reviewed local runtime paths configured, `NativeSkillWriter` invokes the installed Hermes **`skill_manage(action="create" | "edit")` tool itself**. This is native skill creation from a deterministic compiler, not an extra LLM generation call. The tool's existing write gate and content scanner remain in the path.

`gtm/learning.py`가 집계 수치를 스킬 문서로 만듭니다. 설치된 Hermes 경로가 설정되면 실제 `skill_manage` 도구로 생성·수정합니다. 별도의 모델 호출을 추가하지 않아 이 단계의 모델 비용은 0달러입니다. 임의의 댓글을 그대로 스킬 지시문으로 바꾸지 않습니다.

When native paths are empty, the same validated file is generated with `mechanism="compiled"`; this means Hermes-compatible format only. `mechanism="native_tool"` is returned only after the native tool succeeds and the written bytes match. A native tool failure never silently claims success through a fallback.

네이티브 경로가 비어 있으면 동일한 형식의 문서만 만들고 `compiled`로 표시합니다. 실제 도구 실행 및 파일 검증을 통과해야 `native_tool`로 표시합니다. 실패를 성공으로 숨기지 않습니다.

The installed runtime inspected on 2026-10-24 supports `skill_manage` and the `skills` toolset. Its source uses the profile's `HERMES_HOME/skills`; it does not implement the newer `skills.create_dir` option described in the current website. The integration therefore supplies a dedicated private Hermes home. It does not modify the user's global Hermes or Codex skills.

2026-10-24에 확인한 설치 버전은 전용 `HERMES_HOME/skills`를 사용합니다. 최신 문서의 `skills.create_dir` 지원 여부와 설치 버전 사이에 차이가 있어, 전용 비공개 홈을 사용합니다. 기존 전역 스킬은 변경하지 않습니다.

## Configuration / 설정

The committed default keeps native paths empty because Cloud locations differ:

```toml
[learning]
enabled = true
hermes_source_dir = ""
hermes_python = ""
```

For native creation, set both fields to the absolute paths of the reviewed Hermes source checkout and its Python environment **on the Cloud host**. Local verification used `/Users/cgcorp/.hermes/hermes-agent` and its `venv/bin/python`; those macOS paths must not be copied into Linux deployment configuration.

Cloud 호스트에서 실제 설치 경로를 확인한 뒤 두 필드를 설정합니다. 로컬 검증에 사용한 macOS 경로를 Linux 설정에 복사하면 안 됩니다. 설정을 바꾸면 기존 승인 해시가 달라지므로 실제 게시 전 기존 승인 절차를 다시 거칩니다.

The generated skill is under `<database directory>/learning/hermes/skills/disanchor-marketing-feedback/SKILL.md`. SQLite revision history lives beside that dedicated Hermes home. It is private runtime state and excluded from Git and the deploy ZIP.

생성 파일과 변경 이력은 데이터베이스 옆 `learning` 폴더에 비공개로 보관하며 Git이나 배포 ZIP에 넣지 않습니다. 모델 단계는 임의의 스킬 파일을 시스템 권한으로 읽는 대신, 해시 검증을 통과한 구조화된 학습 결과만 사용합니다.

```sh
# Initialize once; this does not start a campaign.
python -m gtm --config config.toml init
# Read existing local aggregates, with no new API request.
python -m gtm --config config.toml feedback
# Create/update the initial skill; no extra model call.
python -m gtm --config config.toml learn
```

The scheduled `collect` and `daily` commands refresh learning automatically. Run the commands above with the same reviewed configuration and database as the scheduler. / 위 명령은 스케줄러와 같은 설정·DB로 실행합니다. 이후 `collect`와 `daily`가 학습을 자동 갱신합니다. 아직 관측이 없으면 표본 부족 상태의 초기 스킬만 생성합니다.

## Evidence and limits / 근거와 해석 한계

- A reusable performance lesson requires at least three comparable posts and at least 100 measured exposures per post. Account, registry fingerprint, platform, hypothesis, hook and denominator remain separate.
- Engagement events per exposure can exceed 100%; events are not unique people. Views, reach and impressions are different denominators.
- Sentiment comes from the feedback collector's available comments, with positive, negative, mixed and unknown counts. `null` means unavailable; it is never rewritten as zero comments or neutral sentiment.
- A lesson is directional evidence for another hypothesis, not proof of causality or a guaranteed viral strategy. Early sparse data creates a baseline skill with no winning-content claims.
- Only closed field names, bounded counts, known hooks, safe account keys and SHA-256 provenance enter the skill. Raw comments, handles, credentials and free-form provider instructions do not.

재사용할 성과 학습에는 비교 가능한 게시물 3개 이상과 각 게시물의 노출 100회 이상이 필요합니다. 계정·플랫폼·분모가 다르면 합쳐서 비교하지 않습니다. 댓글이 없거나 조회할 수 없으면 감정을 추정하지 않으며, 관찰된 경향만 다음 기획의 가설로 사용합니다. 초기 데이터가 부족한 경우에도 기본 스킬은 생성하지만 “잘 터지는 공식”을 지어내지 않습니다.

## Safety, provenance and recovery / 안전장치·근거·복구

The native child clears inherited environment variables before importing Hermes. It has no provider credentials. Network calls, subprocesses and writes outside its dedicated home are denied; only a fixed skill name and compiled content are sent to the native tool. The private configuration enables Hermes' content scanner. An existing changed configuration, another skill or a credential file blocks the native writer instead of being overwritten.

네이티브 실행기는 상속 환경변수를 비우고 외부 통신·하위 프로세스·전용 홈 밖 쓰기를 차단합니다. 댓글이나 학습 결과로 게시 권한, 계정 연결, 스케줄, 예산 또는 보안 설정을 바꿀 수 없습니다.

The child has a 30-second runtime and 64-KiB output bound. Python audit hooks are defense in depth for a reviewed Hermes installation, not an OS sandbox for hostile code; no OS memory limit is configured. / 실행 시간은 30초, 출력은 64KiB로 제한합니다. 검토된 Hermes 설치본에 대한 Python audit hook 보호이며, 악성 코드를 위한 OS 샌드박스나 메모리 상한은 아닙니다.

The evidence fingerprint determines an immutable revision. Identical evidence does not create another revision or another native call. The audit log and revisions are append-only. Rollback restores a known revision and adds an audit event. Manual changes to the active skill block automatic updates and lesson consumption until explicitly resolved. A crash between file and database writes leaves an integrity mismatch and no consumable lessons.

동일 근거는 동일 리비전을 사용해 반복 생성하지 않습니다. 리비전과 감사 기록은 덮어쓰지 않으며, 롤백도 기록됩니다. 수동 수정이나 중간 장애로 파일과 이력이 다르면 해당 학습을 사용하지 않습니다. 정책 또는 계정 바인딩이 바뀐 학습과 168시간보다 오래된 학습도 소비하지 않습니다.

Research/planning receive at most two lessons and 1,000 UTF-8 bytes. Learned content cannot approve a draft. The 168-hour window, ten-post cap, per-account approach, exact-text approval, official API checks, the USD 20 total budget and the smaller model allocation remain enforced by the runner. Paid model work remains disabled until the separate runtime and provider spending controls are verified.

리서치·기획에 전달하는 데이터는 최대 학습 2개, UTF-8 1,000바이트입니다. 학습 문서는 초안을 승인하지 않습니다. 168시간·게시물 10개·정확한 문구 승인·API 확인·전체 20달러 예산과 모델 예산이 계속 적용됩니다. 유료 모델 호출은 별도 비용 한도 검증 전까지 꺼져 있습니다.

The standalone paid `hermes curator run` background model is not scheduled by this integration. Native skill creation is already automatic; starting a second paid, broadly scoped curator without ledger integration would defeat the spending controls.

별도의 유료 `hermes curator run`은 예약하지 않습니다. 현재 자동 스킬 생성은 추가 모델 비용 없이 작동하며, 다른 유료 유지관리 작업을 추가하려면 먼저 같은 비용 장부에 연결해야 합니다.

## Verification / 검증

`tests/test_learning.py` exercises evidence thresholds, missing sentiment, stale bindings, prompt injection exclusion, duplicate evidence, revision integrity, append-only history, rollback, symlink rejection and the isolated native worker's network/process/write restrictions. An actual installed-Hermes smoke test creates, edits and rolls back a skill using clearly marked simulated aggregate evidence; `reports/native-skill-verification.json` records the result. This is tool integration verification, not a live audience performance result.

실제 설치된 Hermes 도구의 생성·수정·롤백까지 로컬에서 검증합니다. 테스트의 집계 데이터는 시뮬레이션이며 실제 마케팅 성과로 사용하지 않습니다. 외부 게시나 유료 모델 호출은 수행하지 않습니다.

Official references: [Hermes Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [official skill manager source](https://github.com/NousResearch/hermes-agent/blob/main/tools/skill_manager_tool.py), [Hermes Curator](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator).
