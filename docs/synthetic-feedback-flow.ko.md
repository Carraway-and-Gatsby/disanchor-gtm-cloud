# 합성 반응으로 확인한 전체 흐름과 Cloud 시작 조건

## 검증 결과

임시 디렉터리의 별도 계정 바인딩·DB·skill 저장소에서 **MCP 반입 → 제외 판정 → sentiment skill 갱신 → Telegram 명령 수신·응답 대기열·전송 확인**을 연속 검증했습니다. 실제 운영 DB·비공개 토큰·실제 반응·실제 봇을 사용하지 않았습니다. 실행기는 Python socket 접근을 차단합니다. 고정된 모의 게시 영수증과 반응을 넣으며 실제 게시 작업은 만들지 않습니다.

| 순서 | 합성 입력 | 반입 / 제외 | 현재 유효한 교훈 | Telegram 확인 |
| --- | --- | --- | --- | --- |
| 1 | 글 3개, 각 100회 노출, 긍정 댓글 5개 + 미확인 1개 | 3 / 0 | 긍정 방향 1개 생성 | `/mcp`, `/excluded`, `/sentiment` 모두 응답 |
| 2 | 각 글의 분류 가능한 댓글 4개 | 0 / 3 | 0개; 앞선 긍정 교훈 제거 | 표본 부족 사유·교훈 0개 |
| 3 | X 댓글 응답의 멘션 출처 확인 필드 없음 | 0 / 3 | 계속 0개 | 권한·출처 미확인 사유·교훈 0개 |
| 4 | 충분한 부정 댓글 표본 | 3 / 0 | 부정 방향 1개로 갱신 | 긍정 0·부정 1개 표시 |

한 단계마다 세 명령을 가짜 개인 소유자 메시지로 입력하고 실제 polling·인증·offset·outbox 코드를 거쳐 가짜 Telegram API의 성공 응답을 확인합니다. **12개 명령 응답**이 모두 `SENT`가 됐습니다. 자동 상태 알림은 별도이며 명령 응답 수에 합산하지 않았습니다. 조회가 MCP를 다시 호출하지 않는 것도 확인했습니다.

이 검증은 MCP 서버·실제 Telegram 통신·Cloud native `skill_manage` 실행 검증이 아닙니다. MCP·신원·Telegram transport는 주입한 가짜 구현이며 skill 갱신은 실제 compiler가 native 호환 형식으로 수행합니다. 실제 봇 연결 검증은 [기존 휴대폰 확인 결과](telegram-mcp-monitoring.ko.md)와 구분합니다. 보고서의 10월 27~28일 시각도 fixture의 가상 시계이며 운영 일정이 아닙니다.

부정 방향은 다음 글을 공격적이거나 부정적으로 쓰라는 뜻이 아닙니다. 근거가 충분할 때 확신·홍보 강도를 낮추고, 혼란을 준 설명을 구체화하고, 독자가 제기한 문제를 검토할 신호입니다. 이번 테스트는 교훈의 갱신·전달까지 검증하며 모델의 실제 문안 개선 효과는 주장하지 않습니다. 댓글 원문·작성자·주입 지시는 DB·skill·Telegram 출력에 남지 않는지 검사합니다.

- [재현 실행기](../scripts/test-feedback-flow.py)
- [전체 흐름 테스트](../tests/test_feedback_flow.py)
- [합성 데이터 검증 근거·Telegram 응답 예시](evidence/synthetic-feedback-flow-2026-10-25.json)

```sh
/opt/homebrew/bin/python3.12 scripts/test-feedback-flow.py
```

표준 오류에 테스트 결과, 표준 출력에 합성 결과 JSON이 나옵니다. 임시 DB·skill은 종료 시 삭제됩니다. 실제 환경 파일은 읽지 않습니다. Cloud 미생성·총예산 **$20**·**API 게시 OFF**·**LinkedIn 차단**·유료 모델 OFF를 유지합니다.

전체 467개 테스트, 소스 보안 preflight, 자격증명 없는 staging smoke가 통과했습니다. 공개 대상 파일을 현재 비공개 환경변수 값과 대조한 결과 일치 항목은 없었습니다.

## Hermes Cloud는 언제 시작할까

**권고: 다음 단계는 사용자 결정 후 게시 없는 24시간 staging 점검입니다. 지금 바로 7일 학습 운영을 시작할 필요는 없습니다.** 로컬 흐름은 검증됐지만 실제 호스트 보안·영속 저장소·재시작 복구·native skill runtime은 아직 확인되지 않았고, 실제 학습 표본도 없습니다. 이번 요청에서는 인스턴스를 생성하지 않습니다.

| 단계 | 시작 조건 | 확인할 내용과 종료 조건 |
| --- | --- | --- |
| 24시간 staging | 사용자가 Cloud 생성을 지시하고 현재 요금·잔액·정지 시각을 확인 | 소셜·모델 키 없는 초기 배포, 비관리자 실행·관리자 인증·영속 저장소 검사. 이후 별도로 전용 Telegram을 연결할 때 로컬 수신기와 중복 실행하지 않기. 재시작 후 DB·offset 복구와 모바일 조회 확인 후 인스턴스 정지 |
| 7일 읽기·학습 운영 | staging 통과, 읽기 전용 수집의 실제 권한·본인 게시 영수증·청구 검증 확보, 운영 시작에 대한 별도 사용자 지시 | 계정·지표가 같은 그룹의 글 최소 3개, 글당 노출 최소 100·분류 댓글 최소 5·분류율 50% 이상인 근거가 있어야 학습 가능. 부족하면 학습 제외를 유지하며 7일 내내 실행할 이유를 재검토 |
| 실제 게시 | 이번 범위에서 계속 차단 | Cloud 시작·skill 갱신·Telegram 응답이 게시 승인을 대신하지 않음. LinkedIn도 계속 차단 |

실제 권한을 확인하려고 테스트 글을 게시하거나 보류 중인 한국어 Threads를 재연결하지 않습니다. 기존 본인 글에 적격 반응이 생기기 전에는 7일 가동을 미루는 편이 비용 대비 낫습니다. 실제 Cloud에서의 모든 검사가 로컬 검증으로 대체되는 것은 아닙니다.

예산은 합계 $20입니다. 기존 계획가($0.60/일 실행·$0.13/일 정지 보관)가 그대로라면 24시간 staging + 7일 실행 + 7일 보관 + 모델 $5·도구 $2 예약은 **$12.71**, 잔여 여유는 $7.29입니다. 모델·도구 예약은 지금 지출하거나 활성화한다는 뜻이 아닙니다. 생성 직전 실제 포털 요금과 세금·별도 구독료까지 다시 계산하고, 일일 비용 확인·정지 시각을 정합니다. $14 경고·$18 선택 유료 작업 차단은 제공자의 청구 상한이나 서버 자동 정지가 아닙니다.

[공식 Cloud 안내](https://portal.nousresearch.com/cloud)는 실행 중 compute+storage, 정지 중 storage 과금과 모델·도구 별도 과금, 청구가 1~2일 뒤 보일 수 있음을 명시합니다(2026-10-25 확인). 개별 인스턴스의 현재 가격은 로그인한 배포 화면에서 다시 확인해야 합니다. 새 구독·충전·자동 충전은 실행하지 않았습니다.

배포 시에는 [한국어 체크리스트](deployment-checklist.ko.md)의 검증된 전체 커밋 SHA로 실행 코드를 고정합니다. 과거 배포 준비 때 복사해 둔 명령 대신 최신 체크리스트를 확인하세요. 실제 계정 안전성은 빈도·중복·광고성·무단 자동 답글·DM 차단과 경고 시 중단으로 관리하며 정지 위험 0을 보장하지 않습니다.

## English summary

Four consecutive synthetic cycles validate MCP import, sparse/permission exclusions, compiled skill creation/invalidation/update, and all three Telegram query round trips through fake transports. Temporary state and a socket-blocked runner prevent mixing with production or sending messages. Cloud remains uncreated, USD 20 unchanged, API publishing OFF and LinkedIn blocked. Recommend a user-authorized 24-hour staging check first; defer seven-day audience learning until host checks, actual read permissions, verified own-post receipts and sufficient evidence are available.
