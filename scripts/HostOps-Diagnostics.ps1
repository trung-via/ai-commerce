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
    # Read the existing TCP listen table. Filtering by LocalPort at the
    # cmdlet level can raise an error when port 9333 is simply not listening.
    try {
        $null = Get-Command Get-NetTCPConnection -ErrorAction Stop
        $listener = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object {
                $_.LocalPort -eq 9333 -and $_.LocalAddress -eq '127.0.0.1'
            }).Count -gt 0
    } catch {
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
