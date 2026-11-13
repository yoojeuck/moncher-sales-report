# 별도 판매일보 예약 작업만 새로 등록하고 기존 이관 감시기는 변경하지 않습니다.
[CmdletBinding()]
param([switch]$ValidateOnly, [switch]$PrepareOnly, [switch]$FunctionsOnly)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'

function New-SalesScheduleConfig([string]$RuntimeRoot,[string]$GitPath) {
  [ordered]@{schema_version=1;repository='yoojeuck/moncher-sales-report';workflow='daily-scrape.yml';ref='main';timezone='Korea Standard Time';slots=@('05:07','07:07','09:07');git_path=$GitPath;runtime_root=$RuntimeRoot;api_version='2026-03-10'}
}

function Get-SalesFutureTriggerBoundaries([DateTimeOffset]$Now) {
  $kst=[TimeZoneInfo]::ConvertTime($Now,[TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time'))
  foreach($slot in @('05:07','07:07','09:07')) {
    $at=[DateTimeOffset]::Parse($kst.ToString('yyyy-MM-dd')+'T'+$slot+':00+09:00')
    if ($at -le $Now) {$at=$at.AddDays(1)}
    $at.ToString('o')
  }
}

function Test-SalesOwnedTaskDefinition($Task,[string]$Root,[string]$Invoker,[string]$Command) {
  return ($Task.TaskName -ceq 'MONFFICE-Sales-Report-Scheduler' -and $Task.Actions.Count -eq 1 -and $Task.Actions[0].WorkingDirectory -ceq $Root -and $Task.Actions[0].Execute -ceq $Command -and $Task.Actions[0].Arguments.Contains(('"'+$Invoker+'"')) -and $Task.Actions[0].Arguments.Contains(('"'+(Join-Path $Root 'scheduler.config.json')+'"')))
}

if ($FunctionsOnly) { return }
$created=$false;$preexistingAbsent=$false;$root=$null;$invokePath=$null;$pwsh=$null
try {
  if (-not $IsWindows -or $PSVersionTable.PSVersion -lt [Version]'7.5') { throw 'WINDOWS_POWERSHELL_75_REQUIRED' }
  if ([TimeZoneInfo]::Local.Id -cne 'Korea Standard Time') { throw 'WINDOWS_TIMEZONE_MUST_BE_KST' }
  $taskName='MONFFICE-Sales-Report-Scheduler'
  $existing=@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop|Where-Object {$_.TaskName -ceq $taskName})
  if ($existing.Count) { throw 'SALES_TASK_ALREADY_EXISTS_NO_REPLACE' }
  $preexistingAbsent=$true
  $root=[IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'MONFFICE\SalesReportScheduler'))
  if ((Test-Path -LiteralPath $root) -and @(Get-ChildItem -LiteralPath $root -Force -ErrorAction Stop).Count) { throw 'INSTALL_ROOT_NOT_EMPTY' }
  $source=Join-Path $PSScriptRoot 'invoke_sales_report_schedule.ps1'
  if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'INVOKER_SOURCE_MISSING' }
  $git=(Get-Command git.exe -CommandType Application -ErrorAction Stop|Select-Object -First 1).Source
  $pwsh=Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'PowerShell\7\pwsh.exe'
  if (-not (Test-Path -LiteralPath $pwsh -PathType Leaf)) { throw 'INSTALLED_POWERSHELL7_MISSING' }
  $config=New-SalesScheduleConfig $root $git
  $sha=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
  $boundaries=@(Get-SalesFutureTriggerBoundaries ([DateTimeOffset]::UtcNow))
  if ($ValidateOnly) { [ordered]@{valid=$true;task_name=$taskName;install_root=$root;slots=$config.slots;first_boundaries=$boundaries;timezone=$config.timezone;source_sha256=$sha;prepare_only=[bool]$PrepareOnly;credentials_read=$false;task_changes=0}|ConvertTo-Json -Compress;exit 0 }
  New-Item -ItemType Directory -Path $root -Force -ErrorAction Stop|Out-Null
  $invokePath=Join-Path $root 'invoke_sales_report_schedule.ps1'
  Copy-Item -LiteralPath $source -Destination $invokePath -ErrorAction Stop
  if ((Get-FileHash -LiteralPath $invokePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $sha) { throw 'INSTALLED_SCRIPT_HASH_MISMATCH' }
  $configPath=Join-Path $root 'scheduler.config.json'
  [IO.File]::WriteAllText($configPath,($config|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
  $validation=@(& $pwsh -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -File $invokePath -ConfigPath $configPath -ValidateOnly 2>&1)
  if ($LASTEXITCODE -ne 0 -or $validation.Count -ne 1 -or -not (($validation[0]|ConvertFrom-Json -DateKind String).valid)) { throw 'INSTALLED_INVOKER_VALIDATION_FAILED' }
  $action=New-ScheduledTaskAction -Execute $pwsh -Argument ('-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -ConfigPath "{1}"' -f $invokePath,$configPath) -WorkingDirectory $root
  $triggers=@($boundaries|ForEach-Object {New-ScheduledTaskTrigger -Daily -At ([DateTimeOffset]::Parse($_).LocalDateTime)})
  $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
  $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -Hidden
  $settings.Enabled=$false
  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -ErrorAction Stop|Out-Null
  $created=$true
  $task=Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop
  if (-not (Test-SalesOwnedTaskDefinition $task $root $invokePath $pwsh) -or -not $task.Settings.Hidden -or -not $task.Settings.StartWhenAvailable -or -not $task.Settings.WakeToRun -or $task.Triggers.Count -ne 3 -or $task.Settings.Enabled -ne $false) { throw 'REGISTERED_TASK_CONTRACT_MISMATCH' }
  $actualTimes=@($task.Triggers|ForEach-Object {[DateTimeOffset]::Parse($_.StartBoundary).ToString('HH:mm')}|Sort-Object)
  if (($actualTimes -join ',') -cne '05:07,07:07,09:07' -or $task.Actions[0].Execute -cne $pwsh) { throw 'REGISTERED_TASK_TIME_OR_COMMAND_MISMATCH' }
  if (@($task.Triggers|Where-Object {[DateTimeOffset]::Parse($_.StartBoundary) -le [DateTimeOffset]::UtcNow}).Count) {throw 'FIRST_TRIGGER_BOUNDARY_NO_LONGER_FUTURE'}
  if (-not $PrepareOnly) {
    Enable-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop|Out-Null
    $task=Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop
    if (-not $task.Settings.Enabled) {throw 'NEW_TASK_ENABLEMENT_NOT_CONFIRMED'}
  }
  [ordered]@{installed=$true;prepare_only=[bool]$PrepareOnly;enabled=$task.Settings.Enabled;wake_to_run=$task.Settings.WakeToRun;task_name=$taskName;runtime_root=$root;script_sha256=$sha;slots=$config.slots;first_boundaries=$boundaries;timezone=$config.timezone;start_requested=$false;existing_tasks_changed=0;credential_persisted=$false}|ConvertTo-Json -Compress
} catch {
  $safe=if ($_.Exception.Message -match '^[A-Z][A-Z0-9_]+$') {$_.Exception.Message} else {'SALES_SCHEDULE_INSTALL_FAILED'}
  $rollbackAttempted=$false;$taskRemoved=$null;$rollbackError=$null
  if ($preexistingAbsent) {
    try {
      $remaining=@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop|Where-Object {$_.TaskName -ceq 'MONFFICE-Sales-Report-Scheduler'})
      if ($remaining.Count -eq 0) {$taskRemoved=$true}
      elseif ($remaining.Count -eq 1 -and $root -and $invokePath -and $pwsh -and (Test-SalesOwnedTaskDefinition $remaining[0] $root $invokePath $pwsh)) {
        $rollbackAttempted=$true
        Unregister-ScheduledTask -TaskName 'MONFFICE-Sales-Report-Scheduler' -TaskPath '\' -Confirm:$false -ErrorAction Stop
        $after=@(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop|Where-Object {$_.TaskName -ceq 'MONFFICE-Sales-Report-Scheduler'})
        $taskRemoved=($after.Count -eq 0)
        if (-not $taskRemoved) {$rollbackError='NEW_TASK_STILL_PRESENT'}
      } else {$taskRemoved=$false;$rollbackError='REFUSED_TO_REMOVE_UNOWNED_TASK'}
    } catch {$taskRemoved=$false;$rollbackError='NEW_TASK_ROLLBACK_NOT_CONFIRMED'}
  }
  [ordered]@{installed=$false;code=$safe;existing_tasks_changed=0;new_task_registered=$created;new_task_rollback_attempted=$rollbackAttempted;new_task_removed=$taskRemoved;rollback_error=$rollbackError;prepared_root_remaining=$root}|ConvertTo-Json -Compress
  exit 1
}
