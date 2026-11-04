#Requires -Version 5.1
<#
AI Commerce personal Windows topology: two EF02 runners and one isolated HostOps.
LOCAL interactive owner-only Install; Plan and Status are read-only.
NEVER stop, unregister, replace or edit the existing AI-COMMERCE-EF02-01.
Do not put runner registration tokens into public Issues or workflow artifacts.
#>
[CmdletBinding()]
param([ValidateSet('Plan', 'Install', 'Status')][string]$Mode = 'Plan')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = 'trung-via/ai-commerce'
$repoUrl = 'https://github.com/trung-via/ai-commerce'
$existingRoot = 'C:\TOOL\ai-commerce-runner'
$existingTask = 'AI-Commerce-EF02-Interactive'
$targets = @(
    [pscustomobject]@{ Name = 'AI-COMMERCE-EF02-02'; Root = 'C:\TOOL\ai-commerce-runner-ef02-02'; Label = 'ai-commerce-ef02'; Task = 'AI-Commerce-EF02-02-Interactive' },
    [pscustomobject]@{ Name = 'AI-COMMERCE-HOSTOPS-01'; Root = 'C:\TOOL\ai-commerce-runner-hostops-01'; Label = 'ai-commerce-hostops'; Task = 'AI-Commerce-HostOps-01-Interactive' }
)
function Assert-LocalOwner {
    if ($env:GITHUB_ACTIONS -eq 'true') { throw 'LOCAL_ATTENDED_ONLY_NOT_GITHUB_ACTIONS' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'ELEVATED_OWNER_POWERSHELL_REQUIRED' }
    if ((Get-Process -Id $PID).SessionId -eq 0) { throw 'INTERACTIVE_WINDOWS_SESSION_REQUIRED' }
    if (-not (Test-Path -LiteralPath (Join-Path $existingRoot 'run.cmd') -PathType Leaf)) { throw 'EXISTING_EF02_01_NOT_FOUND' }
    $old = Get-ScheduledTask -TaskName $existingTask -ErrorAction SilentlyContinue
    if ($null -eq $old -or $old.Principal.LogonType -ne 'Interactive') { throw 'EXISTING_EF02_TASK_UNPROVEN' }
    $account = [string]$old.Principal.UserId
    if ($account.StartsWith('.\')) { $account = $env:COMPUTERNAME + '\' + $account.Substring(2) }
    elseif ($account -notmatch '[\\@]') { $account = $env:COMPUTERNAME + '\' + $account }
    try { $oldSid = ([Security.Principal.NTAccount]::new($account)).Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { throw 'EXISTING_EF02_ACCOUNT_UNRESOLVED' }
    if ($oldSid -ne $identity.User.Value) { throw 'SID_MISMATCH_WITH_EXISTING_EF02' }
    return $identity.Name
}
function Invoke-GhJson {
    param([string]$Endpoint, [switch]$Post)
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'LOCAL_GH_CLI_REQUIRED' }
    if ($Post) { $raw = & gh api --method POST $Endpoint 2>$null }
    else { $raw = & gh api $Endpoint 2>$null }
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($raw | Out-String))) { throw 'GH_REPO_ADMIN_AUTH_REQUIRED' }
    return (($raw | Out-String) | ConvertFrom-Json)
}
function Get-RunnerInventory {
    $response = Invoke-GhJson -Endpoint "repos/$repo/actions/runners?per_page=100"
    if ([int]$response.total_count -gt 100) { throw 'RUNNER_LIST_PAGINATION_REVIEW_REQUIRED' }
    return $response
}
function Assert-NoConflicts {
    $inventory = Get-RunnerInventory
    foreach ($target in $targets) {
        if (Test-Path -LiteralPath $target.Root) { throw "TARGET_ROOT_EXISTS_RECONCILE_FIRST: $($target.Name)" }
        if ($null -ne (Get-ScheduledTask -TaskName $target.Task -ErrorAction SilentlyContinue)) { throw "TARGET_TASK_EXISTS_RECONCILE_FIRST: $($target.Name)" }
        if (@($inventory.runners | Where-Object { $_.name -eq $target.Name }).Count -ne 0) { throw "TARGET_GITHUB_RUNNER_EXISTS_RECONCILE_FIRST: $($target.Name)" }
    }
}
function Get-VerifiedRunnerRelease {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/actions/runner/releases/latest' -Headers @{ 'User-Agent' = 'AI-Commerce-Runner-Installer' } -TimeoutSec 30
    $assets = @($release.assets | Where-Object { $_.name -match '^actions-runner-win-x64-[0-9]+\.[0-9]+\.[0-9]+\.zip$' })
    if ($assets.Count -ne 1) { throw 'WINDOWS_X64_RUNNER_RELEASE_NOT_UNIQUE' }
    $asset = $assets[0]
    $url = [string]$asset.browser_download_url
    $digest = [string]$asset.digest
    if ($url -notmatch '^https://github\.com/actions/runner/releases/download/v[0-9]+\.[0-9]+\.[0-9]+/actions-runner-win-x64-[0-9]+\.[0-9]+\.[0-9]+\.zip$' -or $digest -notmatch '^sha256:[a-fA-F0-9]{64}$') {
        throw 'RUNNER_RELEASE_URL_OR_SHA256_UNVERIFIED'
    }
    return [pscustomobject]@{ Url = $url; Sha256 = $digest.Substring(7).ToLowerInvariant() }
}
function Install-One {
    param([pscustomobject]$Target, [pscustomobject]$Release, [string]$Owner)
    $scratch = Join-Path $env:TEMP ('ai-commerce-runner-setup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $scratch | Out-Null
    try {
        $archive = Join-Path $scratch 'runner.zip'
        Invoke-WebRequest -Uri $Release.Url -OutFile $archive -UseBasicParsing
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Release.Sha256) {
            throw 'RUNNER_ARCHIVE_SHA256_MISMATCH'
        }
        New-Item -ItemType Directory -Path $Target.Root -ErrorAction Stop | Out-Null
        Expand-Archive -LiteralPath $archive -DestinationPath $Target.Root -ErrorAction Stop
        $config = Join-Path $Target.Root 'config.cmd'
        $runnerCmd = Join-Path $Target.Root 'run.cmd'
        if (-not (Test-Path -LiteralPath $config -PathType Leaf) -or -not (Test-Path -LiteralPath $runnerCmd -PathType Leaf)) { throw 'RUNNER_ENTRYPOINT_MISSING' }
        $ticket = Invoke-GhJson -Endpoint "repos/$repo/actions/runners/registration-token" -Post
        $token = [string]$ticket.token
        if ([string]::IsNullOrWhiteSpace($token)) { throw 'RUNNER_REGISTRATION_TOKEN_UNAVAILABLE' }
        Push-Location $Target.Root
        try {
            & $config --unattended --url $repoUrl --token $token --name $Target.Name --labels $Target.Label --work '_work'
            if ($LASTEXITCODE -ne 0) { throw 'RUNNER_CONFIGURATION_FAILED_RECONCILE_FIRST' }
        } finally {
            $token = $null
            Pop-Location
        }
        $launcher = Join-Path $Target.Root 'Runner-Hidden.vbs'
        $lines = @(
            'Set sh = CreateObject("WScript.Shell")',
            ('sh.CurrentDirectory = "{0}"' -f $Target.Root),
            ('exitCode = sh.Run("cmd.exe /d /c """"{0}""""", 0, True)' -f $runnerCmd),
            'WScript.Quit exitCode'
        )
        [IO.File]::WriteAllLines($launcher, [string[]]$lines, [Text.Encoding]::ASCII)
        $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\wscript.exe') -Argument ('//B //Nologo "{0}"' -f $launcher) -WorkingDirectory $Target.Root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $Owner
        $principal = New-ScheduledTaskPrincipal -UserId $Owner -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $Target.Task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description ('AI Commerce runner ' + $Target.Label) | Out-Null
        Start-ScheduledTask -TaskName $Target.Task -ErrorAction Stop
        Write-Host ('REGISTERED_AND_TASK_STARTED=' + $Target.Name)
    } finally {
        Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
    }
}
function Show-Status {
    $inventory = $null
    try { $inventory = Get-RunnerInventory }
    catch { Write-Host 'GITHUB_RUNNER_STATUS=UNKNOWN_CHECK_LOCAL_GH_AUTH' }
    foreach ($target in $targets) {
        $task = Get-ScheduledTask -TaskName $target.Task -ErrorAction SilentlyContinue
        $remote = @()
        if ($null -ne $inventory) { $remote = @($inventory.runners | Where-Object { $_.name -eq $target.Name }) }
        $remoteState = if ($remote.Count -eq 1) { ([string]$remote[0].status + '/busy=' + [string]$remote[0].busy) } else { 'NOT_PROVEN' }
        $localState = if ($null -eq $task) { 'NOT_INSTALLED' } else { [string]$task.State }
        $installed = Test-Path -LiteralPath (Join-Path $target.Root 'run.cmd') -PathType Leaf
        Write-Host ('RUNNER={0} ROOT_INSTALLED={1} TASK={2} GITHUB={3}' -f $target.Name, $installed, $localState, $remoteState)
    }
    Write-Host 'EXISTING_EF02_01=NOT_MODIFIED'
}
if ($Mode -eq 'Plan') {
    Write-Host 'THREE_RUNNER_PLAN_ONLY_NO_EFFECTS'
    Write-Host 'EXISTING=AI-COMMERCE-EF02-01 (unchanged)'
    foreach ($target in $targets) { Write-Host ('NEW={0} LABEL={1} TASK={2}' -f $target.Name, $target.Label, $target.Task) }
    Write-Host 'NO_CPU_RAM_QUOTAS_CONFIGURED'
    return
}
if ($Mode -eq 'Status') { Show-Status; return }
$owner = Assert-LocalOwner
Assert-NoConflicts
$release = Get-VerifiedRunnerRelease
Write-Host 'TWO_NEW_RUNNERS_INSTALL_BEGIN_NO_CPU_RAM_LIMITS'
try {
    foreach ($target in $targets) { Install-One -Target $target -Release $release -Owner $owner }
} catch {
    Write-Host ('INSTALL_HOLD_RECONCILE_LOCAL_AND_GITHUB_STATE: ' + $_.Exception.Message)
    throw
}
Show-Status
Write-Host 'RUNNERS_INSTALLED_VERIFY_BOTH_ONLINE_BEFORE_HOSTOPS_ROUTE_SWITCH'
