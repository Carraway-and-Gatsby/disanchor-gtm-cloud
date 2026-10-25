# Telegram MCP 반입·제외·sentiment 조회

소유자 개인 대화에서 아래 세 명령으로 **저장된 근거**를 확인합니다. 조회 명령은 Upload-Post 수집, skill 생성·갱신, 모델 실행, SNS 게시를 시작하지 않습니다. Cloud는 생성하지 않았고, 총예산 **$20**, **API 게시 OFF**, **LinkedIn 게시 차단**, 유료 모델 OFF를 유지합니다. 로컬 테스트 수신기는 종료됐으며 상시 응답하지 않습니다.

| 명령 | 확인할 내용 | 해석할 때 주의할 점 |
| --- | --- | --- |
| `/mcp` | 최근 반입 시각(KST), 완료·보류·중단 상태, 반입·제외 글 수, MCP·신원 조회 횟수 | 최근 회차의 기록이며 현재 사용 가능한 학습 수가 아님. 48시간이 지나면 과거 기록으로 표시 |
| `/excluded` | 최근 회차의 고정된 제외 사유와 해당 글 수 | 댓글 수·사람 수가 아님. 집계가 없다는 사실은 권한·표본이 충분하다는 뜻이 아님 |
| `/sentiment` | 현재 검증을 통과한 sentiment 교훈 수와 긍정·부정·혼합 방향, 기존 피드백·skill 생성 이력 | 최대 2개 전달 교훈의 요약. 생성 이력과 현재 유효한 학습을 구분하며, 전체 독자의 감정 비율·효과를 뜻하지 않음 |

`/help`와 `/status`에도 세 명령이 안내됩니다. 봇은 게시·승인·계정 재연결·예산 변경·shell 실행을 지원하지 않습니다.

## 현재 실제 결과

실험은 미시작·미승인 상태입니다. 실제 로컬 `collect-mcp` 확인은 `skipped / no_started_experiment`로 끝났고, **반입 0개·제외 0개·MCP 요청 0회·신원 조회 0회·모델 0회·SNS 게시 0회**입니다. 이 보류 결과만 비공개 감사 기록에 저장했습니다.

- `/mcp`: 수집 보류, 실험 미시작, 외부 조회 0회.
- `/excluded`: 확인된 글별 제외 집계 없음. 권한 확인이나 충분한 표본을 의미하지 않음.
- `/sentiment`: 사용 가능한 sentiment 교훈 0개. 실제 이용자 반응을 학습했다고 주장하지 않음.

미확인 권한·부족한 표본을 학습에서 제외하는 조건은 [MCP 자동 반입](mcp-feedback-ingestion.ko.md)에 있습니다. 한국어 Threads `@technodemocracystudio`는 보류 상태를 유지합니다.

## 읽기 전용 경로와 정보 보호

수집기는 반입 결과를 비공개 SQLite의 수정·삭제가 차단된 `mcp_import_events`에 남깁니다. 필드는 상태·허용된 사유 코드·제한된 집계·계정 바인딩 fingerprint 등으로 한정합니다. 정상 반입 기록은 해당 반입의 최종 transaction에 함께 저장합니다. Telegram은 현재 계정 바인딩과 실행 모드에 맞는 최근 기록을 읽습니다. 같은 수집 구간의 중복 실행은 유용한 이전 결과를 덮어쓰지 않습니다.

끝나지 않은 `CLAIMED` 작업은 성공으로 표시하지 않습니다. 알 수 없는 필드·잘못된 집계·미래 시각·손상된 기록은 확인 필요 상태로 처리하며 provider 오류 원문은 보내지 않습니다. 과거 기록은 시각과 함께 구분합니다.

`/sentiment`는 skill DB를 `mode=ro&immutable=1`로 열고 기존 skill의 정책·계정 바인딩·hash·유효 기간·최신 SQLite 근거 일치를 다시 검사합니다. 확인되지 않은 journal 상태는 읽기를 차단합니다. 조회 중 skill 디렉터리·DB를 만들거나 skill 내용을 수정하지 않습니다. 예전에 생성한 skill도 권한 거절·표본 부족·만료·근거 불일치가 생기면 사용 가능한 학습으로 표시하지 않습니다.

Telegram 송수신에 필요한 Bot API 통신과 offset·outbox 저장은 수행합니다. 댓글 원문·작성자·native 게시 ID·계정 fingerprint·skill 원문·키·개인 숫자 ID는 응답이나 공개 점검 근거에 넣지 않습니다. 인증된 소유자의 개인 대화만 허용하며 전달 메시지·그룹·봇·명령 뒤 추가 지시는 무시합니다. 계정 보호를 위해 수집 권한을 임의 확대하거나 SNS 게시·답글·DM을 켜지 않습니다.

## 실제 Telegram 왕복 검증

2026-10-25 소유자가 보낸 `/mcp`, `/excluded`, `/sentiment`를 각각 읽었고, 세 응답 모두 Bot API 전송 성공과 **휴대폰 수신 확인**을 받았습니다. 첫 테스트의 polling 통신 실패 후 같은 DB의 offset·outbox를 보존해 복구했습니다. 모호한 전송을 자동 재전송하거나 기존 상태를 초기화하지 않았습니다. 복구 실행은 추가 시작 알림 없이 로컬 short polling(`timeout=0`)을 사용했습니다. 일반 Cloud 작업의 기본 5초 polling은 유지합니다.

최종 성공 실행은 Telegram API 8회(봇·webhook 확인 2회, 수신 조회 3회, 명령 응답 3회)를 사용했습니다. 실험·게시 초안·게시 작업·비용 원장은 변경하지 않았습니다. 영구 Telegram 설정은 OFF이고 수신기는 종료됐습니다. 이는 로컬 연결 검증이며 Cloud 배포·상시 운영 검증은 아닙니다. [정제된 검증 근거](evidence/telegram-mcp-monitoring-2026-10-25.json)를 참고하세요.

재검증이 필요할 때만 저장소에서 실행합니다. 토큰은 기존 비공개 파일에서 읽으며 명령행이나 채팅에 넣지 않습니다.

```sh
/opt/homebrew/bin/python3.12 scripts/test-telegram.py --monitoring --seconds 300
```

휴대폰의 전용 봇 개인 대화에 세 명령을 각각 보냅니다. 이미 시작 알림을 확인한 복구 상황에서는 `--no-notice`를 추가할 수 있습니다. 세 명령 응답 성공 또는 기본 5분·최대 8회 전송 시도·오류에 도달하면 종료합니다. 실행 시간 옵션의 상한은 10분입니다. Cloud·cron·영구 활성화는 생성하지 않습니다. 자세한 전송 중복 방지·오류 처리는 [Telegram 운영 안내](telegram.md)를 참고하세요.

## 자동 테스트

전체 **466개** 테스트가 통과했습니다. 새 조회 테스트는 실제 수집·모델·학습 호출 금지, 조회 투영 중 skill 파일·DB 변경 없음, 사유 정제, 다른 계정·과거·미래 기록, 미완료 반입, 권한 거절 후 이전 skill 무효화, 손상·만료 skill, 소유자 인증·명령 주입·중복 응답 방지를 검증합니다. 정상 학습 방향·권한 거절·부족 표본은 격리된 가짜 자료로 검사하며 실제 이용자 학습 근거로 저장하지 않습니다. 소스 보안 preflight와 자격증명 없는 staging smoke도 통과했습니다.

공개 대상 파일과 전체 도달 가능한 Git 이력을 현재 비공개 환경변수 값과 대조했으며 일치 항목은 없었습니다. `state/runtime.env`는 권한 600이고 상태 DB·로컬 계정 바인딩·테스트 결과와 함께 Git에서 제외됩니다. 이는 현재 보관 중인 값의 비교 및 소스 검사 결과이며 알려지지 않은 과거 키까지 부재를 보증하는 것은 아닙니다.

## English summary

The owner-only Telegram bot now exposes `/mcp` for persisted import status, `/excluded` for closed exclusion reasons counted by post, and `/sentiment` for currently valid sentiment lessons. Queries never collect Upload-Post data, create/update skills, run models or publish. Skill reads revalidate integrity, account/policy binding, freshness and current evidence through a read-only database handle. Interrupted imports cannot appear successful. Three real owner-command round trips and phone delivery were verified after recovering a local polling failure; the temporary listener is stopped. Real audience learning remains empty. Cloud is uncreated; USD 20, API publishing OFF, LinkedIn blocked and paid models OFF remain enforced.
