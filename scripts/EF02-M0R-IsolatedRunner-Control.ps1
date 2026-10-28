# M0-R isolated VM only. Default Inspect is read-only.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param(
  [ValidateSet('Inspect','InstallWatchdog','Watchdog','ArmOutage')]
  [string]$Mode = 'Inspect',
  [long]$RunId = 0,
  [switch]$AttestDedicatedVM,
  [string]$Acknowledgement = ''
)
$ErrorActionPreference = 'Stop'
$RunnerName = 'AI-COMMERCE-M0R-ISOLATED-01'
$ServiceName = 'actions.runner.trung-via-ai-commerce.AI-COMMERCE-M0R-ISOLATED-01'
$RunnerRoot = 'C:\TOOL\ai-commerce-m0r-isolated-runner'
$GitHubUrl = 'https://github.com/trung-via/ai-commerce'
$TaskName = 'AICommerce-M0R-IsolatedRunner-Recover'
$MarkerFile = Join-Path $RunnerRoot 'm0r-isolated-outage.json'
$StableScript = Join-Path $RunnerRoot 'm0r-isolated-runner-control.ps1'

function Assert-IsolatedRunner {
  if (-not (Test-Path -LiteralPath $RunnerRoot -PathType Container)) {
    throw 'ISOLATED_RUNNER_ROOT_NOT_FOUND'
  }
  $configFile = Join-Path $RunnerRoot '.runner'
  $serviceFile = Join-Path $RunnerRoot '.service'
  if (-not (Test-Path -LiteralPath $configFile -PathType Leaf) -or
      -not (Test-Path -LiteralPath $serviceFile -PathType Leaf)) {
    throw 'ISOLATED_RUNNER_NOT_REGISTERED_AS_SERVICE'
  }
  $cfg = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
  if ($cfg.agentName -cne $RunnerName -or $cfg.gitHubUrl -cne $GitHubUrl) {
    throw 'ISOLATED_RUNNER_CONFIG_IDENTITY_MISMATCH'
  }
  if ((Get-Content -LiteralPath $serviceFile -Raw).Trim() -cne $ServiceName) {
    throw 'ISOLATED_SERVICE_MARKER_MISMATCH'
  }
  # No production/AIOS GitHub runner may share this dedicated VM.
  $services = @(Get-Service -Name 'actions.runner.*' -ErrorAction SilentlyContinue)
  if ($services.Count -ne 1 -or $services[0].Name -cne $ServiceName) {
    throw 'SHARED_WINDOWS_HOST_OR_OTHER_RUNNER_DETECTED'
  }
  $svc = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
  if ($null -eq $svc -or $svc.Name -cne $ServiceName) {
    throw 'ISOLATED_SERVICE_IDENTITY_NOT_CONFIRMED'
  }
  $binPrefix = $RunnerRoot.TrimEnd('\') + '\bin\'
  if (-not $svc.PathName.Contains($binPrefix)) {
    throw 'ISOLATED_SERVICE_BINARY_PATH_MISMATCH'
  }
  if ($svc.StartMode -ne 'Auto') { throw 'ISOLATED_SERVICE_NOT_AUTO_START' }
  return $svc
}

function Assert-Administrator {
  $principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent())
  if (-not $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'ADMINISTRATOR_REQUIRED_FOR_ISOLATED_SERVICE_CONTROL'
  }
}

$service = Assert-IsolatedRunner
if ($Mode -eq 'Inspect') {
  [pscustomobject]@{
    status = 'ISOLATED_LOCAL_GUARDS_PASS_NOT_GITHUB_ONLINE_PROOF'
    runner_name = $RunnerName
    service = $ServiceName
    service_state = $service.State
    startup = $service.StartMode
    host = $env:COMPUTERNAME
    other_runner_on_host = $false
    watchdog_installed = ($null -ne (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue))
    outage_marker_present = (Test-Path -LiteralPath $MarkerFile -PathType Leaf)
    provider_invoked = $false
  } | ConvertTo-Json -Compress
  return
}

if ($Mode -eq 'Watchdog') {
  # Runs independently of GitHub Actions once per minute.
  if (Test-Path -LiteralPath $MarkerFile -PathType Leaf) {
    $marker = Get-Content -LiteralPath $MarkerFile -Raw | ConvertFrom-Json
    if ($marker.service -cne $ServiceName -or $marker.schema -ne 1 -or
        $marker.run_id -notmatch '^[1-9][0-9]*$') {
      throw 'OUTAGE_MARKER_INVALID_NO_AUTOMATIC_ACTION'
    }
    $resume = [DateTimeOffset]::Parse($marker.restore_not_before_utc)
    if ([DateTimeOffset]::UtcNow -lt $resume) {
      Write-Output 'M0R_ISOLATED_WATCHDOG_HOLD_UNTIL_RESTORE_TIME'
      return
    }
  }
  if ($service.State -ne 'Running') {
    Start-Service -Name $ServiceName -ErrorAction Stop
    $service = Assert-IsolatedRunner
    if ($service.State -ne 'Running') {
      throw 'ISOLATED_RUNNER_RESTART_NOT_CONFIRMED'
    }
  }
  if (Test-Path -LiteralPath $MarkerFile -PathType Leaf) {
    Remove-Item -LiteralPath $MarkerFile -Force
  }
  Write-Output 'M0R_ISOLATED_WATCHDOG_SERVICE_RUNNING_NOT_GITHUB_ONLINE_PROOF'
  return
}

Assert-Administrator
if ($Mode -eq 'InstallWatchdog') {
  if (-not $PSCmdlet.ShouldProcess($ServiceName, 'Install isolated restart task')) {
    return
  }
  if (-not $PSCommandPath) { throw 'SCRIPT_FILE_REQUIRED' }
  if (-not [IO.Path]::GetFullPath($PSCommandPath).Equals(
      [IO.Path]::GetFullPath($StableScript), [StringComparison]::OrdinalIgnoreCase)) {
    Copy-Item -LiteralPath $PSCommandPath -Destination $StableScript -Force
  }
  # SYSTEM task survives loss of Runner.Listener. Does not control other services.
  $action = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "' + $StableScript + '" -Mode Watchdog'
  & schtasks.exe /Create /SC MINUTE /MO 1 /TN $TaskName /TR $action /RU SYSTEM /RL HIGHEST /F | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'ISOLATED_WATCHDOG_TASK_INSTALL_FAILED' }
  if ($null -eq (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop)) {
    throw 'ISOLATED_WATCHDOG_NOT_READABLE'
  }
  Write-Output 'M0R_ISOLATED_WATCHDOG_INSTALLED_NO_SERVICE_STOP'
  return
}

if ($Mode -ne 'ArmOutage') { throw 'INVALID_MODE' }
if (-not $AttestDedicatedVM -or $RunId -le 0 -or
    $Acknowledgement -cne 'STOP-ISOLATED-M0R-ONLY') {
  throw 'DEDICATED_VM_ATTESTATION_RUN_ID_AND_ACK_REQUIRED'
}
if ($service.State -ne 'Running') { throw 'ISOLATED_SERVICE_NOT_RUNNING' }
if (Test-Path -LiteralPath $MarkerFile -PathType Leaf) {
  throw 'PRIOR_OUTAGE_MARKER_PRESENT_NO_SECOND_STOP'
}
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task -or $task.State -eq 'Disabled') {
  throw 'INDEPENDENT_RECOVERY_WATCHDOG_NOT_ARMED'
}
if (-not $PSCmdlet.ShouldProcess($ServiceName,
    'Stop ONLY the isolated runner after independent recovery is armed')) {
  return
}
$marker = @{
  schema = 1
  service = $ServiceName
  run_id = [string]$RunId
  restore_not_before_utc = [DateTimeOffset]::UtcNow.AddMinutes(12).ToString('o')
}
$bytes = [Text.Encoding]::UTF8.GetBytes(($marker | ConvertTo-Json -Compress))
$stream = [IO.File]::Open($MarkerFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
Stop-Service -Name $ServiceName -ErrorAction Stop
Write-Output ('M0R_ISOLATED_SERVICE_STOP_ISSUED run_id=' + $RunId)
Write-Output 'NO_PROVIDER_OR_GITHUB_ONLINE_STATUS_CLAIM'
