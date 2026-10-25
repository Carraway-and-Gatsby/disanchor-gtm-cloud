# 북미 게시 시간표와 예약 설정

2026-10-25 변경. X·Threads `@disanchor_com`만을 위한 **향후 자동 운영 시간표**다. 시각은 계정 실적으로 검증된 최적 시간이 아닌 초기 탐색 가설이다. 새 글의 예약·게시·캠페인 시작은 수행하지 않는다. 이미 승인받아 수동 게시한 테스트는 각 1건이며 자동 캠페인과 별도로 기록했다.

| 계정 | 요일 | 동부 ET | 서부 PT | 한국 KST — 미국 서머타임 적용 시 |
| --- | --- | --- | --- | --- |
| 공식 X `@disanchor_com` | 화·목 | 12:30 | 09:30 | 다음 날 01:30 |
| 공식 X `@disanchor_com` | 수 | 15:30 | 12:30 | 다음 날 04:30 |
| Threads `@disanchor_com` | 수·목 | 12:00 | 09:00 | 다음 날 01:00 |
| Threads `@disanchor_com` | 화 | 15:00 | 12:00 | 다음 날 04:00 |

시간대는 `America/New_York`이며 날짜마다 UTC·PT·KST로 변환한다. 2026-11-01 미국 서머타임 종료 후 한국 시각은 표보다 한 시간 늦어진다. 고정 UTC 오프셋으로 예약하지 않는다.

## 코드에서 실제로 적용하는 방식

- [config.toml](../config.toml)의 `[schedule] mode="north_america_weekly"`가 기준이다. **168시간 안에 X 3건·Threads 3건·총 6건 이하**, 플랫폼별 뉴욕 날짜당 1건이다. 기존 수동 테스트 2건은 이 DB에 가짜로 편입하지 않는다.
- 시작 전에 지나간 슬롯은 시작 10분·20분 뒤로 옮기지 않는다. 다음 정규 슬롯을 사용하고 168시간 종료 이후의 예약은 만들지 않는다.
- [슬롯 계산](../gtm/config.py)은 요일·시간을 각각 읽고 시간순으로 반환한다. 기존 설정용 `legacy` 모드는 보존하지만 현재 배포 설정은 주간 모드다. `[platforms]`의 단일 `x_time`·`threads_time`은 주간 모드에서 사용하지 않는다.
- [게시 직전 검사](../gtm/engine.py)는 글의 ID·플랫폼·시각·현지 날짜가 계산된 슬롯과 일치하는지 검증한다. 일치하지 않으면 provider 조회·전송 전에 차단한다. 주간 모드에서는 Clone의 시간 제안도 예약 시각을 바꾸지 못한다.
- 기존 [publisher](../gtm/publisher.py)가 **개별 UTC `scheduled_date`**를 사용한다. 주간 시간표를 적용할 때 `addToQueue`는 사용하지 않는다. 본문·대상 검토, 할당량·비용 확인과 게시 잠금 검사는 그대로다.
- 예약 변경은 승인 지문을 바꾼다. 과거 승인을 재사용하지 않는다. 최종 문구가 바뀌면 다시 검토한다.

미리보기만 생성하는 명령:

```bash
python3.12 scripts/preview-schedule.py --start 2026-10-26T00:00:00-04:00
```

[미리보기 도구](../scripts/preview-schedule.py)는 DB·비밀 파일을 읽거나 만들지 않고, API·모델을 호출하지 않는다. 실제 시작일을 정하기 전에는 출력된 날짜를 예약으로 해석하지 않는다.

## Upload-Post 실제 큐와 한계

실제 조회와 갱신 후 다시 읽어 확인했다. **대기 중인 예약 글은 0건**이며 이번 설정 변경으로 새 글을 제출하지 않았다.

| 프로필 | 실제 변경 | 운영에서의 사용 |
| --- | --- | --- |
| `disanchor_x` — 공식 X 전용 | 매일 09·12·17시에서 **화·수·목 12:30, 슬롯당 1건**으로 축소 | 기본 큐의 수요일은 12:30이므로 **수요일 15:30은 개별 예약 시각으로 직접 지정**한다. 자동 운영은 모든 글에 개별 예약 시각을 사용한다. |
| `default` — Threads·다른 보류 채널 공유 | **변경하지 않음** | 공유 큐의 매일 09·12·17시는 이번 Threads 주간 시간표가 아니다. 보류 계정에 영향을 주지 않도록 Threads는 개별 예약 시각만 사용한다. |

Upload-Post 큐는 `slots`와 `days_of_week`를 조합하므로 요일별로 서로 다른 시각을 저장할 수 없다. 두 시각을 넣으면 선택한 모든 요일에 두 슬롯이 생긴다. 코드의 주간 시간표와 provider 기본 큐의 차이를 숨기지 않는다.

## 계속 유지하는 안전 조건

총예산 **$20**, 자동 게시 **OFF** (`auto_publish=false`, `dry_run=true`, 허용 quota=0), 유료 모델 **OFF**, LinkedIn **차단**을 유지한다. 한국어 개인 Threads와 다른 X 계정은 보류, Instagram은 제외다.

계정·정책 경고, 인증·한도 문제는 중단 사유다. 결과 불명 요청을 재전송하거나 놓친 슬롯을 몰아서 게시하지 않는다. 개발 중임을 자연스럽게 밝히며 광고·중복·자동 DM·무단 답글·인위적 반응 교환을 하지 않는다. 이 게시 빈도가 계정 정지를 방지한다는 보장은 아니다.

현재 Cloud staging의 조회 수신 만료·인스턴스 정지 예정은 **2026-10-26 19:00 KST** 그대로다. 이 시간표를 저장했다고 7일 운영이 시작되거나 Cloud 수명이 늘어나지 않는다. 자동 운영 활성화·정지 시각 조정·실제 예약 제출은 별도 작업이다.

근거: [X 시간대 연구](https://sproutsocial.com/insights/best-times-to-post-on-twitter/), [Threads 시간대 연구](https://buffer.com/resources/the-best-time-to-post-on-threads/). 글로벌 현지 시간 분석을 북미 동·서부에 적용한 가설이며, 실제 두 계정의 성과로 검증된 시간은 아니다.

## English summary

The active source configuration now uses an explicit Tuesday–Thursday calendar in `America/New_York`: X at 12:30 / 15:30 / 12:30 and Threads at 15:00 / 12:00 / 12:00. Maximum six new candidate posts in 168 hours, with three per account and one per local day. Missed slots are skipped; Clone cannot move these times. Exact UTC timestamps are checked before provider access.

The dedicated X profile's fallback queue was narrowed to Tuesday–Thursday 12:30. The shared `default` queue was preserved to avoid changing held accounts. Actual automation uses explicit per-post timestamps rather than either generic queue. No posts were scheduled or published by this update. Auto-publishing and paid models remain OFF, LinkedIn remains blocked, and the total budget remains USD 20.
