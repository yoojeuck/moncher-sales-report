# 실제 인증·API·작업 등록 없이 판매일보 시간 경계와 중복 방지 계약을 검증합니다.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'invoke_sales_report_schedule.ps1') -FunctionsOnly
. (Join-Path $PSScriptRoot 'install_sales_report_schedule.ps1') -FunctionsOnly
$checks=0
function Assert-Equal($Actual,$Expected,[string]$Name) { if ($Actual -cne $Expected) {throw "TEST_FAILED_$Name actual=$Actual expected=$Expected"};$script:checks++ }
function Assert-Throws([scriptblock]$Call,[string]$Name) {try {& $Call;throw 'EXPECTED_THROW_MISSING'} catch {if ($_.Exception.Message -eq 'EXPECTED_THROW_MISSING') {throw "TEST_FAILED_$Name"};$script:checks++} }
function New-State { [ordered]@{schema_version=1;slots=[ordered]@{}} }
function New-Run([int64]$Id,[string]$Event,[string]$Started,[string]$Status='completed',[string]$Conclusion='success') { @{id=$Id;workflow_id=270564514;event=$Event;run_started_at=$Started;status=$Status;conclusion=$Conclusion;head_branch='main'} }
function New-EmptyActiveBodies {$b=[ordered]@{};foreach($s in @('queued','in_progress','waiting','pending','requested')){$b[$s]='{"total_count":0,"workflow_runs":[]}'};return $b}
function Get-Decision($Now,$State,[object[]]$Runs) {Get-SalesScheduleDecision $Now $State $Runs (Read-SalesActiveCounts (New-EmptyActiveBodies))}

$before=Get-SalesKstContext ([DateTimeOffset]::Parse('2026-11-12T20:06:59Z'))
Assert-Equal $before.target_date '2026-11-12' 'KST_previous_day'
Assert-Equal ($null -eq $before.slot) $true 'before_first_slot'
$first=Get-SalesKstContext ([DateTimeOffset]::Parse('2026-11-12T20:07:00Z'))
Assert-Equal $first.key '2026-11-13@05:07' 'first_slot_inclusive'
Assert-Equal (Get-SalesKstContext ([DateTimeOffset]::Parse('2026-11-12T22:07:00Z'))).slot '07:07' 'second_slot'
Assert-Equal (Get-SalesKstContext ([DateTimeOffset]::Parse('2026-11-13T00:07:00Z'))).slot '09:07' 'third_slot'
Assert-Equal (Get-SalesKstContext ([DateTimeOffset]::Parse('2026-11-13T14:00:00Z'))).key '2026-11-13@09:07' 'missed_start_catchup'
$now=[DateTimeOffset]::Parse('2026-11-12T22:07:00Z');$state=New-State
Assert-Equal (Get-Decision $now $state @()).action 'dispatch' 'new_slot_dispatches'
Assert-Equal (Get-Decision $now $state @((New-Run 1 'schedule' '2026-11-12T20:10:00Z'))).action 'dispatch' 'pre_06_success_not_final'
Assert-Equal (Get-Decision $now $state @((New-Run 2 'schedule' '2026-11-12T21:00:00Z'))).reason 'target_collected_after_cutoff' 'post_06_success_skips'
Assert-Equal (Get-Decision $now $state @((New-Run 2 'schedule' '2026-11-12T21:00:00Z' 'completed' 'failure'))).action 'dispatch' 'failed_run_not_complete'
Assert-Equal (Get-Decision $now $state @((New-Run 3 'workflow_dispatch' '2026-11-12T21:01:00Z'))).action 'dispatch' 'unowned_manual_other_date_not_proof'
$branchRun=New-Run 6 'schedule' '2026-11-12T21:01:00Z';$branchRun.head_branch='old-branch'
Assert-Equal (Get-Decision $now $state @($branchRun)).action 'dispatch' 'other_branch_not_complete_proof'
foreach($s in @('queued','in_progress','waiting','pending','requested')) {Assert-Equal (Get-Decision $now $state @((New-Run 4 'workflow_dispatch' '2026-11-12T21:01:00Z' $s))).reason 'collection_already_active' ('active_'+$s)}
Assert-Equal (Get-Decision $now $state @((New-Run 4 'workflow_dispatch' '2026-11-12T21:01:00Z' 'new_unknown_state'))).reason 'collection_already_active' 'unknown_state_fail_closed'
Assert-Equal (Get-Decision $now $state @((New-Run 4 'workflow_dispatch' '2026-11-12T21:01:00Z' 'queued'))).active_run_ids[0] 4 'active_id_logged'
Assert-Equal (Read-SalesWorkflowRuns '{"total_count":0,"workflow_runs":[]}').Count 0 'empty_inventory_proven'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":10,"workflow_runs":[]}'} 'partial_inventory_not_absence'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":0}'} 'missing_inventory_not_absence'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":1,"workflow_runs":[{"id":1}]}'} 'partial_row_not_absence'
Assert-Equal (Read-SalesWorkflowRuns '{"total_count":1,"workflow_runs":[{"id":1,"workflow_id":270564514,"head_branch":"main","status":"completed","event":"schedule","created_at":"2026-11-12T21:00:00Z","run_started_at":"2026-11-12T21:00:01Z","conclusion":"success"}]}').Count 1 'valid_inventory_read'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":1,"workflow_runs":[{"id":1,"workflow_id":42,"head_branch":"main","status":"queued"}]}'} 'foreign_workflow_rejected'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":1,"workflow_runs":[{"id":1,"workflow_id":270564514,"status":"queued"}]}'} 'missing_branch_rejected'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":1,"workflow_runs":[{"id":1,"workflow_id":270564514,"head_branch":"main","status":"unknown"}]}'} 'unknown_api_status_rejected'
Assert-Throws {Get-SalesScheduleDecision $now $state @() $null} 'active_counts_missing_fail_closed'
$b=New-EmptyActiveBodies;$b.Remove('queued');Assert-Throws {Read-SalesActiveCounts $b} 'missing_active_query_rejected'
$b=New-EmptyActiveBodies;$b.waiting='{"total_count":1,"workflow_runs":[{"id":90,"workflow_id":270564514,"head_branch":"main","status":"waiting","event":"workflow_dispatch","created_at":"2026-11-12T21:00:00Z","run_started_at":null,"conclusion":null}]}'
$counts=Read-SalesActiveCounts $b
Assert-Equal $counts.total_count 1 'older_active_outside_recent_page_found'
Assert-Equal (Get-SalesScheduleDecision $now $state @() $counts).reason 'collection_already_active' 'older_active_blocks_dispatch'
$script:wouldPost=0
foreach($invalid in @('{}','{"total_count":0,"workflow_runs":null}','{"total_count":1,"workflow_runs":[]}')) {
  $b=New-EmptyActiveBodies;$b.queued=$invalid
  try {$counts=Read-SalesActiveCounts $b;$d=Get-SalesScheduleDecision $now $state @() $counts;if($d.action -eq 'dispatch'){$script:wouldPost++}}catch{}
}
Assert-Equal $script:wouldPost 0 'partial_active_reads_never_post'
$script:wouldPost=0
foreach($missing in @('event','created_at','run_started_at','conclusion')) {
  $row=[ordered]@{id=1;workflow_id=270564514;head_branch='main';status='completed';event='schedule';created_at='2026-11-12T21:00:00Z';run_started_at='2026-11-12T21:00:01Z';conclusion='success'}
  $row.Remove($missing)
  $body=@{total_count=1;workflow_runs=@($row)}|ConvertTo-Json -Depth 5
  try {$r=Read-SalesWorkflowRuns $body;$d=Get-Decision $now $state $r;if($d.action -eq 'dispatch'){$script:wouldPost++}}catch{}
}
Assert-Equal $script:wouldPost 0 'completed_missing_metadata_never_post'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":1,"workflow_runs":[{"id":1,"workflow_id":270564514,"head_branch":"main","status":"completed","event":"schedule","created_at":"not-time","run_started_at":null,"conclusion":"success"}]}'} 'invalid_completed_time_rejected'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":1,"workflow_runs":[{"id":1,"workflow_id":270564514,"head_branch":"main","status":"completed","event":"schedule","created_at":"2026-11-12T21:00:00Z","run_started_at":"2026-11-12T21:00:01Z","conclusion":"unknown"}]}'} 'invalid_completed_conclusion_rejected'
Assert-Throws {Read-SalesWorkflowRuns '{"total_count":2,"workflow_runs":[{"id":1,"workflow_id":270564514,"head_branch":"main","status":"completed","event":"schedule","created_at":"2026-11-12T21:00:00Z","run_started_at":"2026-11-12T21:00:01Z","conclusion":"success"}]}'} 'truncated_recent_page_rejected'
$b=New-EmptyActiveBodies;$b.queued='{"total_count":2,"workflow_runs":[{"id":90,"workflow_id":270564514,"head_branch":"main","status":"queued","event":"workflow_dispatch","created_at":"2026-11-12T21:00:00Z","run_started_at":null,"conclusion":null}]}'
Assert-Equal (Read-SalesActiveCounts $b).total_count 2 'active_per_page_one_preserves_total'
$state.slots['2026-11-13@05:07']=@{target_date='2026-11-12';run_id=5;outcome='accepted'}
Assert-Equal (Get-Decision $now $state @((New-Run 5 'workflow_dispatch' '2026-11-12T21:01:00Z'))).reason 'target_collected_after_cutoff' 'owned_target_success_skips'
$state.slots['2026-11-13@05:07'].target_date='2026-11-11'
Assert-Equal (Get-Decision $now $state @((New-Run 5 'workflow_dispatch' '2026-11-12T21:01:00Z'))).action 'dispatch' 'other_target_not_proof'
$state.slots['2026-11-13@07:07']=@{target_date='2026-11-12';outcome='dispatch_uncertain'}
Assert-Equal (Get-Decision $now $state @()).reason 'slot_already_recorded' 'uncertain_never_reposts'
$state=New-State;$ctx=Get-SalesKstContext $now;$script:postCount=0;$script:persistCount=0
$persist={param($s) $script:persistCount++;if($script:persistCount -eq 1){Assert-Equal $s.slots[$ctx.key].outcome 'intent' 'intent_before_post'}}
$post={param($target) $script:postCount++;Assert-Equal $target '2026-11-12' 'dispatch_exact_target';throw 'SIMULATED_CONNECTION_RESET'}
$entry=Submit-SalesDispatch $state $ctx $now $persist $post
Assert-Equal $entry.outcome 'dispatch_uncertain' 'unknown_response_saved'
Assert-Equal $script:postCount 1 'unknown_posts_once'
Assert-Throws {Submit-SalesDispatch $state $ctx $now $persist $post} 'second_submit_rejected'
Assert-Equal $script:postCount 1 'second_submit_no_post'
$state=New-State
$rejected=Submit-SalesDispatch $state $ctx $now {} {param($target) @{status=403;body='not logged'}}
Assert-Equal $rejected.outcome 'dispatch_rejected' 'http_rejection_distinct_from_uncertain'
Assert-Equal $rejected.http_status 403 'http_rejection_safe_status'
Assert-Equal (Get-Decision $now $state @()).reason 'slot_already_recorded' 'rejection_no_same_slot_retry'
$resolved=Resolve-SalesDispatchResponse 200 '{"workflow_run_id":55,"run_url":"https://api.github.com/repos/yoojeuck/moncher-sales-report/actions/runs/55","html_url":"https://github.com/yoojeuck/moncher-sales-report/actions/runs/55"}'
Assert-Equal $resolved.run_id 55 'new_api_run_id'
Assert-Equal (Resolve-SalesDispatchResponse 204 '').outcome 'accepted_without_run_id' 'legacy_204_no_repost'
Assert-Equal (Resolve-SalesDispatchResponse 204 '').monitor_pending $true 'legacy_204_not_collection_complete'
Assert-Throws {Resolve-SalesDispatchResponse 200 '{"workflow_run_id":55,"run_url":"https://api.github.com/repos/other/repo/actions/runs/55","html_url":"https://github.com/other/repo/actions/runs/55"}'} 'foreign_run_rejected'
Assert-Throws {Resolve-SalesDispatchResponse 200 'garbage'} 'malformed_response_rejected'
$config=New-SalesScheduleConfig 'C:\Users\test\AppData\Local\MONFFICE\SalesReportScheduler' 'C:\Program Files\Git\cmd\git.exe'
Assert-SalesConfig $config;$checks++
Assert-Equal ($config.Keys -match 'token|password|credential|secret').Count 0 'config_contains_no_secrets'
$bad=New-SalesScheduleConfig 'C:\safe' 'C:\git.exe';$bad.repository='other/repo';Assert-Throws {Assert-SalesConfig $bad} 'foreign_repo_rejected'
$bad=New-SalesScheduleConfig 'C:\safe' 'C:\git.exe';$bad.ref='old-commit';Assert-Throws {Assert-SalesConfig $bad} 'old_ref_rejected'
$bad=New-SalesScheduleConfig 'C:\safe' 'C:\git.exe';$bad.slots=@('00:00');Assert-Throws {Assert-SalesConfig $bad} 'other_times_rejected'
Assert-Throws {Invoke-SalesGitHub 'synthetic' '/repos/other/repo/actions/runs/1'} 'other_api_scope_rejected_before_network'
Assert-Throws {Invoke-SalesGitHub 'synthetic' '/repos/yoojeuck/moncher-sales-report/actions/runs/1' 'POST' '{}'} 'wrong_post_target_rejected_before_network'
$future=@(Get-SalesFutureTriggerBoundaries ([DateTimeOffset]::Parse('2026-11-13T00:30:00Z')))
Assert-Equal $future.Count 3 'three_first_boundaries'
Assert-Equal ([DateTimeOffset]::Parse($future[0]).ToString('yyyy-MM-ddTHH:mm')) '2026-11-14T05:07' 'past_first_time_moves_tomorrow'
Assert-Equal (@($future|Where-Object {[DateTimeOffset]::Parse($_) -le [DateTimeOffset]::Parse('2026-11-13T00:30:00Z')}).Count) 0 'no_first_boundary_in_past'
$future=@(Get-SalesFutureTriggerBoundaries ([DateTimeOffset]::Parse('2026-11-12T21:30:00Z')))
Assert-Equal ([DateTimeOffset]::Parse($future[1]).ToString('yyyy-MM-ddTHH:mm')) '2026-11-13T07:07' 'upcoming_slot_stays_today'
$fake=@{TaskName='MONFFICE-Sales-Report-Scheduler';Actions=@(@{WorkingDirectory='C:\safe';Execute='C:\pwsh.exe';Arguments='-File "C:\safe\invoke.ps1" -ConfigPath "C:\safe\scheduler.config.json"'})}
Assert-Equal (Test-SalesOwnedTaskDefinition $fake 'C:\safe' 'C:\safe\invoke.ps1' 'C:\pwsh.exe') $true 'own_rollback_target'
$fake.Actions[0].WorkingDirectory='C:\other'
Assert-Equal (Test-SalesOwnedTaskDefinition $fake 'C:\safe' 'C:\safe\invoke.ps1' 'C:\pwsh.exe') $false 'foreign_rollback_target_preserved'
[ordered]@{ok=$true;checks=$checks;api_requests=0;credential_reads=0;scheduled_task_changes=0}|ConvertTo-Json -Compress
