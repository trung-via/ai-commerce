#Requires -Version 5.1
<#
Human-attended Chrome CDP 9333 for AI Commerce D5.
Modes: Inspect (default; read only), StartCDP (single signed-Chrome launch
only when the existing acquisition policy explicitly permits it),
PrepareChat (existing READY private ledger + enrolled exact chat; NO SEND).
NEVER run from GitHub Actions, a Windows service, or an elevated shell.
No private paths/chat URLs/tokens are printed, written to a public issue,
or passed to an AI Commerce remote task.
#>
[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'StartCDP', 'PrepareChat')]
    [string] $Mode = 'Inspect',
    [string] $ChromeConfig = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-Bounded([string]$Code) {
    Write-Output ('D5_ATTENDED_STATUS=BLOCKED_' + $Code)
    exit 2
}
if ($env:GITHUB_ACTIONS -eq 'true') { Stop-Bounded 'LOCAL_OPERATOR_ONLY' }
if ($env:OS -ne 'Windows_NT') {
    Stop-Bounded 'WINDOWS_ONLY'
}
if (-not $env:LOCALAPPDATA) { Stop-Bounded 'PRIVATE_APPDATA_REQUIRED' }
if ([System.Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
    Stop-Bounded 'INTERACTIVE_SESSION_REQUIRED'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Stop-Bounded 'USE_NON_ELEVATED_POWERSHELL'
}
$checkout = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not (Test-Path -LiteralPath (Join-Path $checkout 'src\ai_commerce\remote\chrome_acquisition.py') -PathType Leaf)) {
    Stop-Bounded 'CHECKOUT_SOURCE_MISSING'
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    Stop-Bounded 'GIT_REQUIRED'
}
$branch = & git -C $checkout rev-parse --abbrev-ref HEAD 2>$null
if ($LASTEXITCODE -ne 0 -or ($branch | Out-String).Trim() -ne 'main') {
    Stop-Bounded 'CHECKOUT_NOT_MAIN'
}
$remote = & git -C $checkout remote get-url origin 2>$null
$allowed = @(
    'https://github.com/trung-via/ai-commerce.git',
    'https://github.com/trung-via/ai-commerce',
    'git@github.com:trung-via/ai-commerce.git'
)
if ($LASTEXITCODE -ne 0 -or $allowed -notcontains ($remote | Out-String).Trim()) {
    Stop-Bounded 'CHECKOUT_REPOSITORY_UNVERIFIED'
}
$env:GIT_OPTIONAL_LOCKS = '0'
$dirty = @(& git -C $checkout status --porcelain=v1 --untracked-files=no 2>$null)
if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
    Stop-Bounded 'CHECKOUT_TRACKED_CHANGES'
}

$python = Join-Path $checkout '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) { Stop-Bounded 'PYTHON_REQUIRED' }
    $python = $command.Source
}
$privateRoot = Join-Path $env:LOCALAPPDATA 'AI-Commerce'
$manifest = Join-Path $privateRoot 'wake-readiness.json'
$previousPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
try {
    # Prefer the source of this exact inspected checkout, never another repo's
    # separately installed editable Python package.
    $env:PYTHONPATH = Join-Path $checkout 'src'
    Push-Location -LiteralPath $checkout
    $argsList = @()
    if ($Mode -eq 'PrepareChat') {
        if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
            Stop-Bounded 'CURRENT_READY_MANIFEST_ABSENT'
        }
        $answer = Read-Host 'Type PREPARE-EXACT-CHAT to inspect/preload only (NO SEND)'
        if ($answer -cne 'PREPARE-EXACT-CHAT') { Stop-Bounded 'HUMAN_DECLINED' }
        $argsList = @('-m', 'ai_commerce.remote.wake_readiness_flow',
            '--flow-config', $manifest, '--opt-in-open-chat')
    } else {
        if ([string]::IsNullOrWhiteSpace($ChromeConfig)) {
            # These are fixed, known names only. No recursive walk of a
            # private Chrome profile, cookie database or unrelated folders.
            $candidates = @(
                (Join-Path $privateRoot 'Chrome-Acquisition\config.json'),
                (Join-Path $privateRoot 'chrome.json'),
                (Join-Path $privateRoot 'chrome-acquisition.json')
            )
            if (Test-Path -LiteralPath $manifest -PathType Leaf) {
                try {
                    $saved = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 |
                        ConvertFrom-Json -ErrorAction Stop
                    if ($saved.version -eq 1 -and
                            -not [string]::IsNullOrWhiteSpace($saved.chrome_config)) {
                        $candidates += [string]$saved.chrome_config
                    }
                } catch {
                    Stop-Bounded 'PRIVATE_MANIFEST_UNVERIFIED'
                }
            }
            $existing = @($candidates | Select-Object -Unique | Where-Object {
                Test-Path -LiteralPath $_ -PathType Leaf
            })
            if ($existing.Count -eq 0) { Stop-Bounded 'EXISTING_PRIVATE_CONFIG_REQUIRED' }
            if ($existing.Count -gt 1) { Stop-Bounded 'PRIVATE_CONFIG_AMBIGUOUS_EXPLICIT_PATH_REQUIRED' }
            $ChromeConfig = [string]$existing[0]
        }
        if (-not [IO.Path]::IsPathRooted($ChromeConfig)) {
            Stop-Bounded 'PRIVATE_CONFIG_MUST_BE_ABSOLUTE'
        }
        # Historic D5 configurations may still target CDP 9222. This
        # accepted operator action is exclusively for 127.0.0.1:9333.
        # Core chrome_acquisition separately enforces the full config shape,
        # file owner ACL, signed Chrome and exact process/port identity.
        try {
            $configPort = Get-Content -LiteralPath $ChromeConfig -Raw -Encoding UTF8 |
                ConvertFrom-Json -ErrorAction Stop
        } catch {
            Stop-Bounded 'PRIVATE_CONFIG_UNREADABLE'
        }
        if ([string]$configPort.cdp_endpoint -cne 'http://127.0.0.1:9333' -and
                [string]$configPort.cdp_endpoint -cne 'http://127.0.0.1:9333/') {
            Stop-Bounded 'CONFIG_NOT_EXACT_9333'
        }
        if ($Mode -eq 'StartCDP') {
            $answer = Read-Host 'Type START-CDP-9333 to launch/reuse signed dedicated Chrome once'
            if ($answer -cne 'START-CDP-9333') { Stop-Bounded 'HUMAN_DECLINED' }
        }
        $operation = if ($Mode -eq 'StartCDP') { 'launch' } else { 'probe' }
        $argsList = @('-m', 'ai_commerce.remote.chrome_acquisition',
            $operation, '--config', $ChromeConfig, '--checkout', $checkout)
        if ($Mode -eq 'StartCDP') { $argsList += '--opt-in' }
    }
    $raw = @(& $python @argsList 2>&1 | ForEach-Object { [string]$_ })
    $code = $LASTEXITCODE
    $candidate = if ($raw.Count) { [string]$raw[-1] } else { '' }
    try {
        $result = $candidate | ConvertFrom-Json -ErrorAction Stop
        $status = [string]$result.status
        if ($Mode -eq 'PrepareChat') {
            if ($status -eq 'READY_FOR_PREPARE' -and $result.wake_sent -eq $false) {
                Write-Output 'D5_ATTENDED_STATUS=READY_FOR_PREPARE_NO_SEND'
                Write-Output ('D5_BROWSER=' + [string]$result.chrome)
                Write-Output ('D5_EXACT_CHAT=' + [string]$result.chat)
                Write-Output ('D5_AUTO_REPAIRED_COUNT=' + @($result.auto_repaired).Count)
                exit 0
            }
            $reason = [string]$result.code
            if ($reason -match '^[A-Z0-9_]{1,90}$') { Stop-Bounded $reason }
            Stop-Bounded 'PREPARE_NOT_VERIFIED'
        }
        if ($code -eq 0 -and $status -eq 'READY_ATTACH' -and
                $result.cdp_verified -eq $true) {
            Write-Output 'D5_ATTENDED_STATUS=CDP_9333_READY'
            Write-Output ('D5_LAUNCHED_NEW_CHROME=' + [string][bool]$result.launched)
            Write-Output 'D5_WAKE_SENT=False'
            exit 0
        }
        if ($code -eq 0 -and $status -eq 'NOT_RUNNING') {
            Stop-Bounded 'CDP_NOT_RUNNING'
        }
    } catch {
        # The existing acquisition CLI returns a fixed error code on stderr.
        $matched = @($raw | Where-Object {
            $_ -match '^CHROME_ACQUISITION_BLOCKED: ([A-Z0-9_]{1,90})$'
        })
        if ($matched.Count -eq 1 -and
                $matched[0] -match '^CHROME_ACQUISITION_BLOCKED: ([A-Z0-9_]{1,90})$') {
            Stop-Bounded $Matches[1]
        }
        Stop-Bounded 'LOCAL_OPERATOR_DIAGNOSTIC_UNAVAILABLE'
    }
    Stop-Bounded 'RESULT_NOT_VERIFIED'
} finally {
    Pop-Location
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $previousPythonPath
    }
}
