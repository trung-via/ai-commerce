#Requires -Version 7.0
<# HostOps v2: read OS metadata only; no raw commands, usernames, paths, tokens or Chrome profile content. #>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('host_inventory_read','d5_deep_read')]
  [string]$Operation
)
$ErrorActionPreference = 'Stop'
$sessionId = (Get-Process -Id $PID -ErrorAction Stop).SessionId
$runnerCount = @(Get-Process -Name 'Runner.Listener' -ErrorAction SilentlyContinue).Count
$chromeCount = @(Get-Process -Name 'chrome' -ErrorAction SilentlyContinue).Count
$tcpKnown = $false
$count9333 = 0
$count9222 = 0
try {
  $connections = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Where-Object { $_.LocalAddress -eq '127.0.0.1' -and ($_.LocalPort -eq 9333 -or $_.LocalPort -eq 9222) })
  $count9333 = @($connections | Where-Object { $_.LocalPort -eq 9333 }).Count
  $count9222 = @($connections | Where-Object { $_.LocalPort -eq 9222 }).Count
  $tcpKnown = $true
} catch {
  # TCP UNKNOWN is different from no listener.
}
$taskState = 'Unknown'
try {
  $task = Get-ScheduledTask -TaskName 'AI-Commerce-EF02-Interactive' -ErrorAction Stop
  $status = [string]$task.State
  if ($status -in @('Ready','Running','Disabled')) { $taskState = $status }
  else { $taskState = 'Other' }
} catch { }
$flagsKnown = $false
$debugFlags = 0
$profileFlags = 0
if ($Operation -eq 'd5_deep_read') {
  try {
    $chromeInfo = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction Stop)
    $flagsKnown = ($chromeInfo.Count -eq 0 -or @($chromeInfo | Where-Object { [string]::IsNullOrWhiteSpace([string]$_.CommandLine) }).Count -eq 0)
    foreach ($p in $chromeInfo) {
      $cmd = [string]$p.CommandLine
      if ($cmd -match '(?i)--remote-debugging-port[= ]9333(?:\s|$)') { $debugFlags++ }
      if ($cmd -match '(?i)--user-data-dir[= ]') { $profileFlags++ }
    }
    if (-not $flagsKnown) { $debugFlags = 0; $profileFlags = 0 }
  } catch {
    $flagsKnown = $false
    $debugFlags = 0
    $profileFlags = 0
  }
}
$result = [ordered]@{
  operation = $Operation
  session_id = [int]$sessionId
  pwsh_major = [int]$PSVersionTable.PSVersion.Major
  chrome_count = [int]$chromeCount
  runner_count = [int]$runnerCount
  cdp_9333_count = [int]$count9333
  cdp_9222_count = [int]$count9222
  tcp_known = [bool]$tcpKnown
  process_flags_known = [bool]$flagsKnown
  d5_debug_flag_count = [int]$debugFlags
  d5_profile_flag_count = [int]$profileFlags
  task_state = $taskState
}
$result | ConvertTo-Json -Compress -Depth 2
