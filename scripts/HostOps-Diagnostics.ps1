#Requires -Version 7.0
<#
Read-only bounded diagnostics for the AI Commerce interactive self-hosted runner.
Never read browser pages, credentials, process command lines, private state,
GitHub tokens, or environment variables. No browser launch or Wake Send.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('runner_readonly','d5_readonly')]
    [string]$Operation
)
$ErrorActionPreference = 'Stop'
$sessionId = (Get-Process -Id $PID).SessionId
$runnerCount = @(Get-Process -Name 'Runner.Listener' -ErrorAction SilentlyContinue).Count
$chromeCount = @(Get-Process -Name 'chrome' -ErrorAction SilentlyContinue).Count
$listener = $false
if ($Operation -eq 'd5_readonly') {
    # Get-NetTCPConnection reads a TCP table; it does not attach to Chrome or
    # access ChatGPT. Query only the exact D5 port.
    try {
        $listener = @(Get-NetTCPConnection -LocalPort 9333 -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalAddress -eq '127.0.0.1' }).Count -gt 0
    } catch {
        # An unavailable/denied TCP inventory must not be reported as OFF.
        throw 'CDP_TCP_INVENTORY_UNAVAILABLE'
    }
}
$diagnostics = [ordered]@{
    operation = $Operation
    session_id = [int]$sessionId
    pwsh_major = [int]$PSVersionTable.PSVersion.Major
    runner_listener_count = [int]$runnerCount
    chrome_process_count = [int]$chromeCount
    cdp_9333_listening = [bool]$listener
}
$diagnostics | ConvertTo-Json -Depth 2 -Compress
