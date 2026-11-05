#Requires -Version 5.1
<#
Human-attended Chrome CDP 9333 for AI Commerce D5.
Modes: Inspect (default; read only), RepairConfig (one explicitly approved,
Human-attended private port-only config update, NO CHROME LAUNCH),
StartCDP (single signed-Chrome launch only with separate opt-in),
PrepareChat (existing READY private ledger + enrolled exact chat; NO SEND).
NEVER run from GitHub Actions, a Windows service, or an elevated shell.
No private paths/chat URLs/tokens are printed, written to a public issue,
or passed to an AI Commerce remote task.
#>
[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'RepairConfig', 'StartCDP', 'PrepareChat')]
    [string] $Mode = 'Inspect',
    [string] $ChromeConfig = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-Bounded([string]$Code) {
    Write-Output ('D5_ATTENDED_STATUS=BLOCKED_' + $Code)
    exit 2
}

# Return ONE approved enum only; never return native stderr, stack traces or paths.
function Get-Repair-Preflight-Code([object[]]$Lines) {
    $allowed = @(
        'BROWSER_OWNERSHIP_UNPROVEN',
        'CDP_ENDPOINT_INVALID',
        'CDP_NOT_LOOPBACK_OWNED',
        'CDP_PROTOCOL_UNAVAILABLE',
        'CHROME_EXECUTABLE_UNTRUSTED',
        'CHROME_PATH_INVALID',
        'CONFIG_DUPLICATE_KEY',
        'CONFIG_INVALID',
        'CONFIG_IN_CHROME_PROFILE',
        'CONFIG_IN_REPOSITORY',
        'CONFIG_NOT_OPERATOR_OWNED',
        'CONFIG_PATH_INVALID',
        'ELEVATED_CHROME_DISALLOWED',
        'LAUNCH_NOT_AUTHORIZED',
        'LAUNCH_OWNER_MISMATCH',
        'LAUNCH_UNCERTAIN',
        'LAUNCH_UNPROVEN',
        'LOCAL_APP_DATA_UNAVAILABLE',
        'OPERATOR_OWNERSHIP_UNPROVEN',
        'POWERSHELL_UNAVAILABLE',
        'PROCESS_IDENTITY_UNKNOWN',
        'PROFILE_ALREADY_ACTIVE_NO_CDP',
        'PROFILE_LOCKED',
        'PROFILE_NOT_DEDICATED',
        'PROFILE_UNAVAILABLE',
        'WINDOWS_INTERACTIVE_REQUIRED',
        'WINDOWS_PROBE_INVALID',
        'WINDOWS_PROBE_UNAVAILABLE',
        'WINDOWS_TOKEN_UNAVAILABLE'
    )
    if ($null -eq $Lines -or $Lines.Count -gt 8) { return 'UNVERIFIED' }
    $seen = @()
    foreach ($entry in $Lines) {
        $value = [string]$entry
        if ($value.Length -gt 4096) { return 'UNVERIFIED' }
        # PowerShell 5.1 may prefix native stderr with "python.exe : ".
        # Only one terminal fixed code is ever allowed into the output.
        if ($value -cmatch 'CHROME_ACQUISITION_BLOCKED: ([A-Z0-9_]{1,90})$') {
            $code = [string]$Matches[1]
            if ($allowed -ccontains $code) { $seen += $code }
        }
    }
    if ($seen.Count -ne 1) { return 'UNVERIFIED' }
    return [string]$seen[0]
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
    if ($Mode -eq 'RepairConfig') {
        # This is an operator-local PRIVATE WRITE, not a GitHub/HostOps remote
        # command. Select exactly one of the three audited literal names.
        # No Issue, manifest, custom -ChromeConfig or recursive path is followed.
        if (-not [string]::IsNullOrWhiteSpace($ChromeConfig)) {
            Stop-Bounded 'REPAIR_FIXED_CANDIDATES_ONLY'
        }
        $known = @(
            (Join-Path $privateRoot 'Chrome-Acquisition\config.json'),
            (Join-Path $privateRoot 'chrome.json'),
            (Join-Path $privateRoot 'chrome-acquisition.json')
        )
        $found = @($known | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
        if ($found.Count -eq 0) { Stop-Bounded 'REPAIR_KNOWN_CONFIG_ABSENT' }
        if ($found.Count -ne 1) { Stop-Bounded 'REPAIR_KNOWN_CONFIG_AMBIGUOUS' }
        $ChromeConfig = [string]$found[0]
        # Reject reparse/junction components and symlinks; never follow them
        # for a private config WRITE, even if the fixed name appears valid.
        try {
            $cursor = [IO.Path]::GetFullPath($ChromeConfig)
            while ($true) {
                $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
                if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    Stop-Bounded 'REPAIR_REPARSE_DENIED'
                }
                $parent = Split-Path -Path $cursor -Parent
                if ([string]::IsNullOrEmpty($parent) -or $parent -eq $cursor) { break }
                $cursor = $parent
            }
        } catch {
            Stop-Bounded 'REPAIR_REPARSE_CHECK_UNAVAILABLE'
        }
        $probeArgs = @('-m', 'ai_commerce.remote.chrome_acquisition',
            'probe', '--config', $ChromeConfig, '--checkout', $checkout)
        # The existing Chrome acquisition layer checks exact local owner ACL,
        # signed Google Chrome and any active/profile-conflicting process.
        # Never change the config when Chrome ownership is uncertain or active.
        try {
            $preflight = @(& $python @probeArgs 2>&1 | ForEach-Object { [string]$_ })
            $preflightExit = $LASTEXITCODE
            if ($preflightExit -ne 0) {
                # A refused native guard is not a generic failure. Forward
                # only the fixed enum from the source-owned Chrome CLI.
                $cause = Get-Repair-Preflight-Code -Lines $preflight
                Stop-Bounded ('REPAIR_PREFLIGHT_' + $cause)
            }
            if ($preflight.Count -ne 1) { Stop-Bounded 'REPAIR_PREFLIGHT_UNVERIFIED' }
            $preflightRecord = $preflight[0] | ConvertFrom-Json -ErrorAction Stop
            if ($preflightRecord.status -cne 'NOT_RUNNING' -or
                    $preflightRecord.browser_accessed -ne $false) {
                Stop-Bounded 'REPAIR_BROWSER_STATE_NOT_IDLE'
            }
            $rawPrivate = [IO.File]::ReadAllText(
                $ChromeConfig, [Text.UTF8Encoding]::new($false, $true))
            $data = $rawPrivate | ConvertFrom-Json -ErrorAction Stop
            $keys = @($data.PSObject.Properties | ForEach-Object { $_.Name })
            $expected = @('version','executable','user_data_dir',
                'profile_directory','cdp_endpoint','allow_launch','exclusive_user_data')
            if ($keys.Count -ne $expected.Count -or
                    @($expected | Where-Object { $keys -cnotcontains $_ }).Count -ne 0) {
                Stop-Bounded 'REPAIR_CONFIG_SCHEMA_CHANGED'
            }
            $prior = [string]$data.cdp_endpoint
            if ($prior -ceq 'http://127.0.0.1:9333' -or
                    $prior -ceq 'http://127.0.0.1:9333/') {
                Write-Output 'D5_ATTENDED_STATUS=CONFIG_9333_ALREADY'
                Write-Output 'D5_WAKE_SENT=False'
                exit 0
            }
            $approval = Read-Host 'Type REPAIR-CONFIG-9333 for PRIVATE config port change ONLY (NO CHROME, NO SEND)'
            if ($approval -cne 'REPAIR-CONFIG-9333') { Stop-Bounded 'HUMAN_DECLINED' }
            # Modify only the endpoint field. The source has passed strict
            # duplicate-key/schema and existing ownership/signature checks.
            $data.cdp_endpoint = 'http://127.0.0.1:9333'
            $stagedText = $data | ConvertTo-Json -Depth 4
            $beforeHash = (Get-FileHash -LiteralPath $ChromeConfig -Algorithm SHA256).Hash
            $dir = [IO.Path]::GetDirectoryName($ChromeConfig)
            $nonce = [guid]::NewGuid().ToString('N')
            $stage = Join-Path $dir ('.ai-commerce-9333-' + $nonce + '.stage')
            $backup = Join-Path $dir ('.ai-commerce-9333-' + $nonce + '.backup')
            $replaced = $false
            try {
                # Stage an empty file under the private folder, copy its exact
                # owner/ACL BEFORE writing any private config bytes.
                $acl = Get-Acl -LiteralPath $ChromeConfig -ErrorAction Stop
                $handle = [IO.File]::Open($stage, [IO.FileMode]::CreateNew,
                    [IO.FileAccess]::Write, [IO.FileShare]::None)
                $handle.Dispose()
                Set-Acl -LiteralPath $stage -AclObject $acl -ErrorAction Stop
                $stagedAcl = Get-Acl -LiteralPath $stage -ErrorAction Stop
                if ($stagedAcl.Sddl -cne $acl.Sddl) {
                    Stop-Bounded 'REPAIR_STAGED_ACL_UNVERIFIED'
                }
                [IO.File]::WriteAllText(
                    $stage, $stagedText, [Text.UTF8Encoding]::new($false))
                # Verify stage via exact same signed Chrome/ACL/native probe.
                $stageArgs = @('-m', 'ai_commerce.remote.chrome_acquisition',
                    'probe', '--config', $stage, '--checkout', $checkout)
                $stageProbe = @(& $python @stageArgs 2>&1 | ForEach-Object { [string]$_ })
                if ($LASTEXITCODE -ne 0 -or $stageProbe.Count -lt 1 -or
                        ($stageProbe[-1] | ConvertFrom-Json).status -cne 'NOT_RUNNING') {
                    Stop-Bounded 'REPAIR_STAGING_UNVERIFIED'
                }
                if ((Get-FileHash -LiteralPath $ChromeConfig -Algorithm SHA256).Hash -cne $beforeHash) {
                    Stop-Bounded 'REPAIR_CONFIG_CHANGED_RECHECK'
                }
                # Win32 replacement preserves target DACL/attributes. Backup
                # stays in private parent and is kept on UNKNOWN readback.
                [IO.File]::Replace($stage, $ChromeConfig, $backup, $false)
                $replaced = $true
                $post = @(& $python @probeArgs 2>&1 | ForEach-Object { [string]$_ })
                if ($LASTEXITCODE -ne 0 -or $post.Count -lt 1 -or
                        ($post[-1] | ConvertFrom-Json).status -cne 'NOT_RUNNING') {
                    Stop-Bounded 'REPAIR_POST_WRITE_UNKNOWN_RECONCILE'
                }
                $verifyData = [IO.File]::ReadAllText(
                    $ChromeConfig, [Text.UTF8Encoding]::new($false, $true)) |
                    ConvertFrom-Json -ErrorAction Stop
                if ([string]$verifyData.cdp_endpoint -cne 'http://127.0.0.1:9333') {
                    Stop-Bounded 'REPAIR_POST_WRITE_UNKNOWN_RECONCILE'
                }
                # Cleanup of the one private backup only after verified result.
                Remove-Item -LiteralPath $backup -Force -ErrorAction Stop
                Write-Output 'D5_ATTENDED_STATUS=CONFIG_9333_UPDATED_NO_LAUNCH'
                Write-Output 'D5_WAKE_SENT=False'
                exit 0
            } finally {
                # Never remove the original or a possibly-needed backup.
                if (-not $replaced -and (Test-Path -LiteralPath $stage -PathType Leaf)) {
                    Remove-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue
                }
            }
        } catch {
            # No exception text, private path, config bytes or backup filename
            # leaves the local operator action. On UNKNOWN, do not retry.
            Stop-Bounded 'REPAIR_FAILED_RECONCILE_LOCAL'
        }
    }
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
