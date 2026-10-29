# Read-only diagnostic for the exact Windows account running the AI Commerce EF02 runner.
# Does NOT call a model, edit settings.json, register a runner, or widen permissions.
[CmdletBinding()]
param([string]$Workspace = (Get-Location).Path)

$ErrorActionPreference = 'Stop'
try {
    $who = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $cli = Get-Command agy -ErrorAction Stop
    $workspaceFull = [IO.Path]::GetFullPath($Workspace)
    if (-not (Test-Path -LiteralPath $workspaceFull -PathType Container)) {
        throw 'WORKSPACE_MISSING'
    }

    # Both flags are documented by AGY. --help and --version must not invoke a model.
    $helpLines = @(& $cli.Source --help 2>&1)
    $helpExit = $LASTEXITCODE
    if ($helpExit -ne 0) { throw 'AGY_HELP_FAILED' }
    $help = $helpLines -join "`n"
    if (-not $help.Contains('--print') -or -not $help.Contains('--add-dir')) {
        throw 'AGY_HEADLESS_WORKSPACE_FLAGS_MISSING'
    }

    $settings = Join-Path $HOME '.gemini\antigravity-cli\settings.json'
    $settingsPresent = Test-Path -LiteralPath $settings -PathType Leaf
    $readAsk = $false
    $readDeny = $false
    if ($settingsPresent) {
        $raw = Get-Content -LiteralPath $settings -Raw -Encoding UTF8
        try {
            $config = ConvertFrom-Json -InputObject $raw -AsHashtable -ErrorAction Stop
        } catch {
            throw 'AGY_SETTINGS_JSON_UNREADABLE'
        }
        if ($config -is [System.Collections.IDictionary]) {
            $p = $config['permissions']
            if ($p -is [System.Collections.IDictionary]) {
                foreach ($rule in @($p['ask'])) {
                    if ($rule -is [string] -and $rule.StartsWith('read_file(')) {
                        $readAsk = $true
                    }
                }
                foreach ($rule in @($p['deny'])) {
                    if ($rule -is [string] -and $rule.StartsWith('read_file(')) {
                        $readDeny = $true
                    }
                }
            }
        }
    }
    Write-Output "RUNNER_ACCOUNT=$who"
    Write-Output "AGY_BINARY_RESOLVED=True"
    Write-Output "AGY_HEADLESS_FLAGS_SUPPORTED=True"
    Write-Output "WORKSPACE_EXISTS=True"
    Write-Output "SETTINGS_PRESENT=$settingsPresent"
    Write-Output "READ_FILE_ASK_RULE_PRESENT=$readAsk"
    Write-Output "READ_FILE_DENY_RULE_PRESENT=$readDeny"
    Write-Output 'READ_FILE_EFFECTIVE_PERMISSION=UNPROVEN_NO_MODEL'
    Write-Output 'AGY_PREFLIGHT_PASS_NO_MODEL'
    exit 0
} catch {
    # Intentionally do not reveal full config, tokens, or untrusted exception contents.
    $reason = $_.Exception.Message
    if ($reason -notmatch '^(WORKSPACE_MISSING|AGY_HELP_FAILED|AGY_HEADLESS_WORKSPACE_FLAGS_MISSING|AGY_SETTINGS_JSON_UNREADABLE)$') {
        $reason = 'PRECHECK_ENV_UNAVAILABLE'
    }
    Write-Error "AGY_PREFLIGHT_FAILED:$reason"
    exit 2
}
