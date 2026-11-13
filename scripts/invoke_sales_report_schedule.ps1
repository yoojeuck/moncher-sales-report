# 판매일보 전용 예약이 최신 main의 원문 수집만 요청하고 중복 요청을 막습니다.
[CmdletBinding()]
param([string]$ConfigPath, [switch]$DryRun, [switch]$ValidateOnly, [switch]$FunctionsOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Get-SalesKstContext([DateTimeOffset]$Now) {
  $kst = [TimeZoneInfo]::ConvertTime($Now, [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time'))
  $slots = @('05:07', '07:07', '09:07')
  $due = @($slots | Where-Object { [TimeSpan]::Parse($_) -le $kst.TimeOfDay })
  $slot = if ($due.Count) { $due[-1] } else { $null }
  [ordered]@{
    today = $kst.ToString('yyyy-MM-dd'); target_date = $kst.AddDays(-1).ToString('yyyy-MM-dd')
    slot = $slot; key = if ($slot) { $kst.ToString('yyyy-MM-dd') + '@' + $slot } else { $null }
    cutoff = [DateTimeOffset]::Parse($kst.ToString('yyyy-MM-dd') + 'T06:00:00+09:00')
  }
}

function Assert-SalesConfig($Config) {
  $expected = @('schema_version','repository','workflow','ref','timezone','slots','git_path','runtime_root','api_version')
  if (@($Config.Keys | Where-Object { $_ -notin $expected }).Count -or @($expected | Where-Object { -not $Config.Contains($_) }).Count) { throw 'CONFIG_KEYS_INVALID' }
  if ($Config.schema_version -ne 1 -or $Config.repository -cne 'yoojeuck/moncher-sales-report' -or $Config.workflow -cne 'daily-scrape.yml' -or $Config.ref -cne 'main' -or $Config.timezone -cne 'Korea Standard Time' -or $Config.api_version -cne '2026-03-10') { throw 'CONFIG_SCOPE_INVALID' }
  if ((@($Config.slots) -join ',') -cne '05:07,07:07,09:07') { throw 'CONFIG_SLOTS_INVALID' }
  if (-not [IO.Path]::IsPathRooted($Config.git_path) -or [IO.Path]::GetFileName($Config.git_path) -ine 'git.exe' -or -not [IO.Path]::IsPathRooted($Config.runtime_root)) { throw 'CONFIG_PATH_INVALID' }
}

function Get-SalesScheduleDecision([DateTimeOffset]$Now, [System.Collections.IDictionary]$State, [object[]]$Runs, [System.Collections.IDictionary]$ActiveCounts) {
  $ctx = Get-SalesKstContext $Now
  if (-not $ctx.slot) { return [ordered]@{ action='skip'; reason='before_first_slot'; context=$ctx } }
  if ($State.slots.Contains($ctx.key)) { return [ordered]@{ action='skip'; reason='slot_already_recorded'; context=$ctx } }
  $statuses=@('queued','in_progress','waiting','pending','requested')
  if (-not $ActiveCounts -or $ActiveCounts.counts -isnot [System.Collections.IDictionary] -or @($statuses|Where-Object {-not $ActiveCounts.counts.Contains($_)}).Count -or @($ActiveCounts.counts.Keys|Where-Object {$_ -notin $statuses}).Count) {throw 'ACTIVE_COUNTS_NOT_PROVEN'}
  $sum=[int64]0
  foreach($s in $statuses) {if ([string]$ActiveCounts.counts[$s] -notmatch '^\d+$') {throw 'ACTIVE_COUNTS_NOT_PROVEN'};$sum += [int64]$ActiveCounts.counts[$s]}
  if ([string]$ActiveCounts.total_count -notmatch '^\d+$' -or [int64]$ActiveCounts.total_count -ne $sum) {throw 'ACTIVE_COUNTS_NOT_PROVEN'}
  if ($sum -gt 0) {return [ordered]@{action='skip';reason='collection_already_active';active_run_ids=@($ActiveCounts.run_ids);active_counts=$ActiveCounts.counts;context=$ctx}}
  $active=@($Runs | Where-Object { $_.status -cne 'completed' })
  if ($active.Count) { return [ordered]@{ action='skip'; reason='collection_already_active'; active_run_ids=@($active|ForEach-Object {[int64]$_.id}); context=$ctx } }
  if ($ctx.slot -ne '05:07') {
    $ownedIds = @($State.slots.Values | Where-Object { $_.target_date -ceq $ctx.target_date -and $_.run_id } | ForEach-Object { [string]$_.run_id })
    foreach ($run in $Runs) {
      if ($run.status -cne 'completed' -or $run.conclusion -cne 'success' -or $run.head_branch -cne 'main') { continue }
      $started = if ($run.run_started_at) { $run.run_started_at } else { $run.created_at }
      if (-not $started) { continue }
      $at = [DateTimeOffset]::Parse([string]$started)
      if ($at -lt $ctx.cutoff -or $at -gt $Now) { continue }
      if ($run.event -ceq 'schedule' -or ([string]$run.id -in $ownedIds)) { return [ordered]@{ action='skip'; reason='target_collected_after_cutoff'; completed_run_id=[int64]$run.id; context=$ctx } }
    }
  }
  [ordered]@{ action='dispatch'; reason='collection_due'; context=$ctx }
}

function Test-SalesIsoTimestamp($Text) {
  if ($Text -isnot [string] -or $Text -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$') {return $false}
  try {$null=[DateTimeOffset]::Parse($Text,[Globalization.CultureInfo]::InvariantCulture);return $true} catch {return $false}
}

function Read-SalesRunsEnvelope([string]$Body, [ValidateSet(1,30)][int]$PageSize=30) {
  try {$data=$Body|ConvertFrom-Json -AsHashtable -Depth 25 -DateKind String} catch {throw 'GITHUB_RUNS_RESPONSE_INVALID'}
  if ($data -isnot [System.Collections.IDictionary] -or -not $data.Contains('workflow_runs') -or $data.workflow_runs -isnot [array] -or -not $data.Contains('total_count') -or [string]$data.total_count -notmatch '^\d+$') {throw 'GITHUB_RUNS_RESPONSE_INVALID'}
  if ([int64]$data.total_count -gt 0 -and $data.workflow_runs.Count -eq 0) {throw 'GITHUB_RUNS_RESPONSE_INCOMPLETE'}
  if ([int64]$data.total_count -lt $data.workflow_runs.Count) {throw 'GITHUB_RUNS_RESPONSE_INCONSISTENT'}
  if ($data.workflow_runs.Count -ne [Math]::Min([int64]$data.total_count,[int64]$PageSize)) {throw 'GITHUB_RUNS_PAGE_INCOMPLETE'}
  foreach($run in $data.workflow_runs) {
    $required=@('id','workflow_id','head_branch','status','event','created_at','run_started_at','conclusion')
    if ($run -isnot [System.Collections.IDictionary] -or @($required|Where-Object {-not $run.Contains($_)}).Count) {throw 'GITHUB_RUNS_ROW_INCOMPLETE'}
    if ([string]$run.id -notmatch '^[1-9][0-9]*$' -or $run.workflow_id -ne 270564514 -or [string]::IsNullOrWhiteSpace($run.head_branch) -or $run.status -notin @('queued','in_progress','waiting','pending','requested','completed') -or $run.event -notin @('schedule','workflow_dispatch') -or -not (Test-SalesIsoTimestamp $run.created_at)) {throw 'GITHUB_RUNS_ROW_INVALID'}
    if ($run.status -ceq 'completed') {
      if ($run.conclusion -notin @('success','failure','neutral','cancelled','skipped','timed_out','action_required','stale','startup_failure') -or -not (Test-SalesIsoTimestamp $run.run_started_at)) {throw 'GITHUB_COMPLETED_RUN_INCOMPLETE'}
      if ([DateTimeOffset]::Parse($run.run_started_at) -lt [DateTimeOffset]::Parse($run.created_at)) {throw 'GITHUB_RUN_TIME_INCONSISTENT'}
    } elseif ($null -ne $run.conclusion -or ($null -ne $run.run_started_at -and -not (Test-SalesIsoTimestamp $run.run_started_at))) {throw 'GITHUB_ACTIVE_RUN_INVALID'}
  }
  return $data
}

function Read-SalesWorkflowRuns([string]$Body) {
  $data=Read-SalesRunsEnvelope $Body
  return ,@($data.workflow_runs)
}

function Read-SalesActiveCounts([System.Collections.IDictionary]$Bodies) {
  $statuses=@('queued','in_progress','waiting','pending','requested')
  if (@($statuses|Where-Object {-not $Bodies.Contains($_)}).Count -or @($Bodies.Keys|Where-Object {$_ -notin $statuses}).Count) {throw 'ACTIVE_RESPONSE_MISSING'}
  $counts=[ordered]@{};$ids=[Collections.Generic.List[int64]]::new();$sum=[int64]0
  foreach($status in $statuses) {
    $data=Read-SalesRunsEnvelope ([string]$Bodies[$status]) 1
    foreach($run in $data.workflow_runs) {if ($run.status -cne $status) {throw 'ACTIVE_RESPONSE_STATUS_MISMATCH'};$ids.Add([int64]$run.id)}
    $counts[$status]=[int64]$data.total_count;$sum += [int64]$data.total_count
  }
  return [ordered]@{counts=$counts;total_count=$sum;run_ids=@($ids|Sort-Object -Unique)}
}

function Resolve-SalesDispatchResponse([int]$StatusCode, [string]$Body) {
  if ($StatusCode -eq 204) { return [ordered]@{ outcome='accepted_without_run_id'; run_id=$null; monitor_pending=$true } }
  if ($StatusCode -ne 200) { throw 'DISPATCH_HTTP_REJECTED' }
  try { $data = $Body | ConvertFrom-Json -AsHashtable -DateKind String } catch { throw 'DISPATCH_RESPONSE_INVALID' }
  if (-not $data.workflow_run_id -or [int64]$data.workflow_run_id -lt 1) { throw 'DISPATCH_RESPONSE_INVALID' }
  $id = [int64]$data.workflow_run_id
  if ($data.run_url -cne "https://api.github.com/repos/yoojeuck/moncher-sales-report/actions/runs/$id" -or $data.html_url -cne "https://github.com/yoojeuck/moncher-sales-report/actions/runs/$id") { throw 'DISPATCH_RESPONSE_SCOPE_INVALID' }
  [ordered]@{ outcome='accepted'; run_id=$id; html_url=$data.html_url }
}

function Submit-SalesDispatch([System.Collections.IDictionary]$State, $Context, [DateTimeOffset]$Now, [scriptblock]$Persist, [scriptblock]$Dispatch) {
  if ($State.slots.Contains($Context.key)) { throw 'SLOT_ALREADY_RECORDED' }
  $entry = [ordered]@{ target_date=$Context.target_date; requested_at=$Now.ToUniversalTime().ToString('o'); outcome='intent'; run_id=$null }
  $State.slots[$Context.key] = $entry
  & $Persist $State
  try {
    $response = & $Dispatch $Context.target_date
    $entry.http_status = [int]$response.status
    $resolved = Resolve-SalesDispatchResponse $response.status $response.body
    foreach ($key in $resolved.Keys) { $entry[$key] = $resolved[$key] }
  } catch {
    $entry.outcome = if ($response -and $response.status -notin @(200,204)) {'dispatch_rejected'} else {'dispatch_uncertain'}
    $entry.safe_error = if ($entry.outcome -eq 'dispatch_rejected') {'GITHUB_DISPATCH_HTTP_REJECTED'} else {'GITHUB_DISPATCH_NOT_CONFIRMED_DO_NOT_REPEAT_SLOT'}
  }
  & $Persist $State
  return $entry
}

function Write-SalesAtomicJson([string]$Path, $Value) {
  $temp = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
  try { [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 15), [Text.UTF8Encoding]::new($false)); [IO.File]::Move($temp, $Path, $true) }
  finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) } }
}

function Get-SalesGitCredential([string]$GitPath) {
  $info = [Diagnostics.ProcessStartInfo]::new($GitPath)
  foreach ($a in @('-c','credential.interactive=false','credential','fill')) { $info.ArgumentList.Add($a) }
  $info.UseShellExecute=$false; $info.CreateNoWindow=$true
  $info.RedirectStandardInput=$true; $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
  $info.Environment['GIT_TERMINAL_PROMPT']='0'; $info.Environment['GCM_INTERACTIVE']='Never'; $info.Environment['GCM_GUI_PROMPT']='0'
  $proc = [Diagnostics.Process]::new(); $proc.StartInfo=$info; $fields=@{}
  try {
    [void]$proc.Start(); $proc.StandardInput.Write("protocol=https`nhost=github.com`n`n"); $proc.StandardInput.Close()
    $stdout=$proc.StandardOutput.ReadToEndAsync(); $stderr=$proc.StandardError.ReadToEndAsync()
    if (-not $proc.WaitForExit(20000)) { $proc.Kill($true); throw 'GITHUB_CREDENTIAL_TIMEOUT' }
    $raw=$stdout.GetAwaiter().GetResult(); $discard=$stderr.GetAwaiter().GetResult()
    if ($proc.ExitCode -ne 0) { throw 'GITHUB_CREDENTIAL_UNAVAILABLE' }
    foreach ($line in $raw.Split("`n")) { if ($line -match '^([^=]+)=(.*)$') { $fields[$matches[1]]=$matches[2].TrimEnd("`r") } }
    if ([string]::IsNullOrWhiteSpace($fields.password)) { throw 'GITHUB_CREDENTIAL_UNAVAILABLE' }
    return [string]$fields.password
  } finally { $raw=$null; $discard=$null; $fields.Clear(); $proc.Dispose() }
}

function Invoke-SalesGitHub([string]$Token, [string]$Path, [string]$Method='GET', [string]$Body) {
  if ($Path -notmatch '^/repos/yoojeuck/moncher-sales-report/actions/(workflows/daily-scrape\.yml/(runs\?(per_page=30|status=(queued|in_progress|waiting|pending|requested)&per_page=1)|dispatches)|runs/[1-9][0-9]*)$') { throw 'GITHUB_REQUEST_SCOPE_INVALID' }
  if (($Method -eq 'POST' -and $Path -cne '/repos/yoojeuck/moncher-sales-report/actions/workflows/daily-scrape.yml/dispatches') -or $Method -notin @('GET','POST')) { throw 'GITHUB_REQUEST_METHOD_INVALID' }
  $headers=@{ Authorization=('Bearer '+$Token); Accept='application/vnd.github+json'; 'X-GitHub-Api-Version'='2026-03-10'; 'User-Agent'='MONFFICE-sales-report-scheduler' }
  try {
    $args=@{Uri=('https://api.github.com'+$Path); Method=$Method; Headers=$headers; TimeoutSec=30; MaximumRedirection=0; SkipHttpErrorCheck=$true}
    if ($Method -eq 'POST') { $args.Body=$Body; $args.ContentType='application/json' }
    $response=Invoke-WebRequest @args
    return [ordered]@{status=[int]$response.StatusCode; body=[string]$response.Content}
  } catch { throw 'GITHUB_REQUEST_FAILED' }
  finally { $headers.Clear() }
}

if ($FunctionsOnly) { return }
$token=$null; $lock=$null; $state=$null; $statePath=$null
try {
  if ($PSVersionTable.PSVersion -lt [Version]'7.5') { throw 'POWERSHELL_75_REQUIRED' }
  if (-not $ConfigPath) { throw 'CONFIG_PATH_REQUIRED' }
  $config = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path) | ConvertFrom-Json -AsHashtable -DateKind String
  Assert-SalesConfig $config
  $root=[IO.Path]::GetFullPath($config.runtime_root)
  if ([IO.Path]::GetFullPath((Split-Path -Parent $ConfigPath)) -ine $root) { throw 'CONFIG_LOCATION_INVALID' }
  if (-not (Test-Path -LiteralPath $config.git_path -PathType Leaf) -or -not (Test-Path -LiteralPath $root -PathType Container)) { throw 'CONFIG_RUNTIME_UNAVAILABLE' }
  if ($ValidateOnly) { [ordered]@{valid=$true; repository=$config.repository; workflow=$config.workflow; ref=$config.ref; slots=$config.slots; timezone=$config.timezone; credential_source='existing Git Credential Manager'; credentials_read=$false; dispatch_requests=0} | ConvertTo-Json -Compress; exit 0 }
  $lock=[IO.File]::Open((Join-Path $root 'scheduler.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
  $statePath=Join-Path $root 'scheduler.state.json'
  $state=if (Test-Path -LiteralPath $statePath -PathType Leaf) { [IO.File]::ReadAllText($statePath)|ConvertFrom-Json -AsHashtable -Depth 20 -DateKind String } else { [ordered]@{schema_version=1;slots=[ordered]@{}} }
  if ($state.schema_version -ne 1 -or $state.slots -isnot [System.Collections.IDictionary]) { throw 'STATE_SCHEMA_INVALID' }
  $now=[DateTimeOffset]::UtcNow
  $context=Get-SalesKstContext $now
  if (-not $context.slot) { [ordered]@{outcome='before_first_slot';dispatch_requests=0}|ConvertTo-Json -Compress; exit 0 }
  $token=Get-SalesGitCredential $config.git_path
  $read=Invoke-SalesGitHub $token '/repos/yoojeuck/moncher-sales-report/actions/workflows/daily-scrape.yml/runs?per_page=30'
  if ($read.status -ne 200) { throw 'GITHUB_RUNS_READ_FAILED' }
  $runs=Read-SalesWorkflowRuns $read.body
  $activeBodies=[ordered]@{}
  foreach($status in @('queued','in_progress','waiting','pending','requested')) {
    $active=Invoke-SalesGitHub $token ('/repos/yoojeuck/moncher-sales-report/actions/workflows/daily-scrape.yml/runs?status='+$status+'&per_page=1')
    if ($active.status -ne 200) {throw 'GITHUB_ACTIVE_RUNS_READ_FAILED'}
    $activeBodies[$status]=$active.body
  }
  $activeCounts=Read-SalesActiveCounts $activeBodies
  $decision=Get-SalesScheduleDecision $now $state @($runs) $activeCounts
  if ($DryRun) { [ordered]@{dry_run=$true;decision=$decision;dispatch_requests=0;credential_persisted=$false}|ConvertTo-Json -Depth 8 -Compress; exit 0 }
  if ($decision.action -eq 'skip') {
    $state.last_check=[ordered]@{at=$now.ToString('o');outcome=$decision.reason;slot=$context.key;target_date=$context.target_date;completed_run_id=$decision.completed_run_id;active_run_ids=$decision.active_run_ids}
    Write-SalesAtomicJson $statePath $state
    $state.last_check|ConvertTo-Json -Compress
    exit 0
  }
  $persist={param($value) Write-SalesAtomicJson $statePath $value}
  $dispatch={param($target) Invoke-SalesGitHub $token '/repos/yoojeuck/moncher-sales-report/actions/workflows/daily-scrape.yml/dispatches' 'POST' (([ordered]@{ref='main';inputs=[ordered]@{target_date=$target;collect_all='false'}})|ConvertTo-Json -Depth 4 -Compress)}
  $entry=Submit-SalesDispatch $state $context $now $persist $dispatch
  if ($entry.outcome -in @('dispatch_uncertain','dispatch_rejected')) { $entry|ConvertTo-Json -Compress; exit 1 }
  if (-not $entry.run_id) { $entry|ConvertTo-Json -Compress; exit 0 }
  $deadline=[DateTimeOffset]::UtcNow.AddMinutes(10)
  do {
    Start-Sleep -Seconds 30
    $observed=Invoke-SalesGitHub $token ('/repos/yoojeuck/moncher-sales-report/actions/runs/'+$entry.run_id)
    if ($observed.status -ne 200) { throw 'GITHUB_RUN_OBSERVATION_FAILED' }
    $run=$observed.body|ConvertFrom-Json -AsHashtable -Depth 20 -DateKind String
    if ([int64]$run.id -ne [int64]$entry.run_id -or $run.repository.full_name -cne $config.repository -or $run.head_branch -cne 'main' -or $run.path -cne '.github/workflows/daily-scrape.yml') { throw 'GITHUB_RUN_SCOPE_INVALID' }
    $entry.status=$run.status; $entry.conclusion=$run.conclusion; $entry.started_at=$run.run_started_at; $entry.updated_at=$run.updated_at
    if ($run.status -eq 'completed') { $entry.outcome=if ($run.conclusion -eq 'success') {'completed'} else {'collection_failed'} }
    Write-SalesAtomicJson $statePath $state
    if ($run.status -eq 'completed') { $entry|ConvertTo-Json -Compress; if ($run.conclusion -eq 'success') {exit 0} else {exit 1} }
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  $entry.outcome='monitor_pending'; $entry.monitor_pending=$true; Write-SalesAtomicJson $statePath $state; $entry|ConvertTo-Json -Compress
  exit 0
} catch {
  $safe=if ($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$') {$_.Exception.Message} else {'SALES_SCHEDULER_FAILED'}
  if ($state -and $statePath) { $state.last_error=[ordered]@{at=[DateTimeOffset]::UtcNow.ToString('o');code=$safe}; try {Write-SalesAtomicJson $statePath $state} catch {} }
  [ordered]@{ok=$false;code=$safe;credential_exposed=$false;automatic_post_retry=$false}|ConvertTo-Json -Compress
  exit 1
} finally { $token=$null; if ($lock) {$lock.Dispose()} }
