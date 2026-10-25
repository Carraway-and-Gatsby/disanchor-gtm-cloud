# Cloud staging Telegram 조회

Cloud staging의 저장 기록은 전용 봇 `@disanchor_gtm_bot`의 **소유자 개인 대화**에서 조회합니다. 일반 Hermes Telegram 채널은 비활성 상태로 유지합니다. 이 수신기는 모델·셸·게시·승인·수집·학습 명령을 실행하지 않습니다.

| 명령 | 표시하는 정보 |
| --- | --- |
| `/status` | 캠페인 상태·게시 집계·모델 상태·예산 기록 |
| `/mcp` | 실제 staging DB에 저장된 MCP 반입 결과 |
| `/excluded` | 저장된 제외 사유. 반입 기록이 없으면 미확인으로 표시 |
| `/sentiment` | 현재 유효한 sentiment 교훈. 근거가 없으면 교훈 없음으로 표시 |

각 답변에는 `Cloud staging · 실제 저장 기록` 표시가 붙습니다. 조회가 수집이나 학습을 새로 실행하지 않으므로, 로컬에서 검증한 합성 데이터나 과거 로컬 결과가 Cloud의 실제 결과처럼 표시되지 않습니다. 합성 테스트는 별도 임시 DB·skill 디렉터리에서만 실행합니다.

## 안전 조건

- 총 운영 예산 **$20**, API 게시 **OFF**, LinkedIn 게시 **차단**, 유료 모델 **OFF** 유지.
- `/status`의 예산 값은 앱의 저장된 장부입니다. Cloud 인프라 청구액을 자동 조회한 값이 아니며 청구 검증 상태를 함께 확인해야 합니다.
- Telegram 자격 정보 3개만 별도 `state/telegram-owner.env`에 저장합니다. 로컬 전체 환경 파일·Upload-Post 키는 Cloud로 복사하지 않습니다.
- 비공개 작업 폴더에서 토큰 파일은 `0600`, state 디렉터리는 `0700`으로 관리합니다. 토큰·소유자 숫자 ID·원문 API 오류는 저장소와 실행 로그에 기록하지 않습니다.
- 정확히 일치하는 네 명령만 허용합니다. 타인·그룹·전달 메시지·다른 실행 지시는 거부합니다. 최초 Cloud 연결 이전에 대기 중이던 오래된 명령은 검증 실적에 포함하지 않습니다.
- 공식 Telegram HTTPS API의 `getMe`, `getWebhookInfo`, `getUpdates`, `sendMessage`만 사용합니다. 기존 webhook이 있으면 변경하지 않고 중단합니다.
- 단일 DB 잠금으로 수신기 중복 실행을 막습니다. Telegram 메시지 전송은 결과 불명 시 재전송하지 않습니다. 전송 시도는 회당 8건·시간당 30건·staging 기간 총 200건 이내입니다.
- 각 수신 실행은 최대 약 40초와 제한된 네트워크 타임아웃으로 끝나며, 별도의 유한 반복 no-agent Cron으로 이어집니다. 종료 시각 이후에는 Telegram 네트워크 요청을 하지 않습니다.
- 조회 전후 모든 캠페인 테이블의 값이 같은지 검사합니다. Telegram offset·응답 outbox·최소 조회 감사 정보만 변경할 수 있습니다.

## 이번 staging의 수명

2026-10-26 **19:00 KST**를 수신 종료 시각으로 고정합니다. Cron 반복 횟수도 유한하게 설정합니다. 기존 24시간 Cloud 정지 예약은 유지합니다. 수신기 만료는 서버를 정지시키지 않으며, 앱의 정지 예약은 provider의 강제 예산 한도가 아닙니다.

새 배포에서는 종료 시각을 새로 검토하고 기존 수신기를 먼저 종료해야 합니다. 같은 봇으로 로컬 테스트 수신기와 Cloud 수신기를 동시에 실행하지 마세요. 토큰은 일반 Hermes 채널이나 대화창에 붙여 넣지 마세요.

## 검증과 증거

오프라인 검증: `python3.12 -m unittest discover -s tests -q` 및 `python3.12 scripts/cloud-preflight.py`.

실제 Cloud에서는 명령을 새로 보내야 수신·응답 왕복을 입증할 수 있습니다. API 전송 성공과 휴대폰 수신 확인은 구분합니다. 비공개 Cloud `reports/cloud-telegram-status.json`에는 명령별 수신·API 전송 성공 집계와 캠페인 불변 여부만 남깁니다. 공개 저장소에는 토큰·소유자 ID·실제 반응 원문·Cloud 비공개 경로를 올리지 않습니다.

구현: [조회 수신기](../gtm/cloud_telegram.py), [명시적 종료 시각 CLI](../scripts/cloud-telegram.py), [안전성 테스트](../tests/test_cloud_telegram.py).
