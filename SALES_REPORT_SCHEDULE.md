# 판매일보 정시 수집 보완

GitHub `schedule` 이벤트가 몇 시간 늦게 생성돼 원문이 아침에 비는 문제를 줄이기 위해, 이 PC의 별도 작업 `MONFFICE-Sales-Report-Scheduler`가 **05:07·07:07·09:07 KST**에 최신 `main`의 `daily-scrape.yml`을 요청한다. 기존 GitHub cron은 백업으로 유지한다. 다우 증분이관 감시기와 예약 revision 15는 바꾸지 않는다.

수집 대상은 KST 기준 어제 날짜 한 날이며 `collect_all=false`다. 첫 회차는 판매일보 06:00 날짜 경계보다 이르므로 07:07 회차에서 완결을 확인한다. 07:07·09:07에는 06:00 이후 성공한 정기 수집 또는 이 예약 작업이 같은 날짜로 요청한 실행이 있으면 추가 수집을 생략한다. 진행 중인 원문 수집이 있어도 중복 요청하지 않는다. 요약 전용 실행은 원문 수집 완료의 근거로 사용하지 않는다.

## 설치와 확인

현재 로그인한 사용자로 PowerShell 7.5 이상에서 실행한다. Windows 시간대가 `Korea Standard Time`이어야 한다.

```powershell
pwsh -NoProfile -File scripts/install_sales_report_schedule.ps1 -ValidateOnly
pwsh -NoProfile -File scripts/install_sales_report_schedule.ps1 -PrepareOnly
pwsh -NoProfile -File "$env:LOCALAPPDATA/MONFFICE/SalesReportScheduler/invoke_sales_report_schedule.ps1" -ConfigPath "$env:LOCALAPPDATA/MONFFICE/SalesReportScheduler/scheduler.config.json" -ValidateOnly
pwsh -NoProfile -File "$env:LOCALAPPDATA/MONFFICE/SalesReportScheduler/invoke_sales_report_schedule.ps1" -ConfigPath "$env:LOCALAPPDATA/MONFFICE/SalesReportScheduler/scheduler.config.json" -DryRun
```

설치기는 호출 스크립트 하나와 비밀 없는 설정만 `%LOCALAPPDATA%/MONFFICE/SalesReportScheduler`로 복사한다. 동일 이름 작업 또는 비어 있지 않은 설치 폴더가 있으면 덮어쓰지 않고 멈춘다. 트리거 세 개, 현재 사용자 Interactive/Limited, Hidden, StartWhenAvailable, **WakeToRun**, IgnoreNew, 실행 제한 20분으로 등록한다. 전역 전원 설정과 기존 작업은 바꾸지 않는다.

`-PrepareOnly`는 **Enabled=false**로 등록하고 직접 시작하지 않는다. 최초 시각은 각 슬롯의 현재 이후 첫 시각으로 명시해 과거 예약이 설치 중 즉시 실행되지 않게 한다. 담당자가 검증된 당일 복구 실행 ID와 대상 날짜를 `scheduler.state.json`에 기록하고, 읽기 전용 검수 후 이 새 작업만 Enable/Start한다. 일반 설치도 먼저 비활성 등록과 계약 검수를 거친 뒤 켜며, `Start-ScheduledTask`를 직접 호출하지 않는다. 기존 다른 작업은 변경하지 않는다.

인증은 기존 Git Credential Manager를 비대화식으로 호출해 메모리에서만 재사용한다. 새 토큰이나 다우·AI 자격을 파일에 저장하지 않는다. GitHub의 해당 저장소 Actions 쓰기 권한이 필요하며, 자격이 없으면 로그인 창을 띄우거나 새 자격을 만들지 않고 오류로 끝난다.

## 중복·실패 경계

`scheduler.state.json`에 날짜/슬롯별 요청 의도를 **POST 전에** 원자 저장한다. 최근 30건 목록만으로 진행 중 0건을 판단하지 않는다. 같은 원문 수집 workflow의 queued·in_progress·waiting·pending·requested를 상태별 `per_page=1` 조회의 `total_count`로 확인하며 하나라도 있으면 중복 요청하지 않는다. 목록·집계 조회 실패나 부분 응답은 빈 목록으로 읽지 않고 POST 없이 종료한다. 같은 슬롯의 요청 의도가 있으면 결과가 불명이어도 다시 POST하지 않는다. 인증·네트워크·응답 오류도 같은 슬롯에서 자동 재시도하지 않는다. 다음에 정해진 예비 슬롯에서 최신 상태를 다시 확인한다.

API `2026-03-10`의 200 응답은 `workflow_run_id`와 해당 저장소 URL을 검증한 뒤 기록한다. 이전 204 응답은 접수됐지만 ID를 모르는 `monitor_pending` 상태로 기록하고 같은 슬롯에서 다시 요청하지 않는다. 수집이 시작되면 30초 간격으로 최대 10분 상태를 읽고 요청/시작/최종 상태를 남긴다. `accepted`는 수집 완료가 아니며 최종 `completed`와 `conclusion=success`를 구분한다. PC 종료나 로그인 해제, GitHub 호스트 자체 대기까지 정시 완료를 보장하는 구조는 아니다. Windows의 놓친 실행은 StartWhenAvailable로 복구한다.

원문·요약 workflow는 같은 `sales-data-main` 큐에서 순차 처리하고 최신 main을 체크아웃한다. 사용자가 도중에 main을 바꿔 원격 게시가 거절되면 강제 push나 재배열로 덮어쓰지 않는다.

## 로컬 검증

```powershell
pwsh -NoProfile -File scripts/test_sales_report_schedule.ps1
```

시험은 시간 경계, 진행 중 작업, 06시 이후 완료 증거, 슬롯 중복, 응답 불명 및 다른 저장소/경로 차단을 합성 자료로 확인한다. 실제 API·자격·예약 작업을 사용하지 않는다.
