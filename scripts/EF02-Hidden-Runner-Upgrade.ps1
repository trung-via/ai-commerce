#Requires -Version 5.1
<#
AI Commerce EF02: hide the interactive runner console without changing its session.
Install stages a new Task Scheduler action without terminating a currently running job.
Status inspects the installed action; Rollback restores the previous action.
Run in interactive Windows PowerShell as Administrator on the runner laptop.
#>
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Status', 'Rollback')]
    [string]$Mode = 'Status',
    [string]$RunnerRoot = 'C:\TOOL\ai-commerce-runner'
)

$ErrorActionPreference = 'Stop'
$taskName = 'AI-Commerce-EF02-Interactive'
$serviceName = 'actions.runner.trung-via-ai-commerce.AI-COMMERCE-EF02-01'
$runnerCmd = Join-Path $RunnerRoot 'run.cmd'
$launcher = Join-Path $RunnerRoot 'EF02-Hidden-Runner.vbs'
$backupFile = Join-Path $RunnerRoot '.ef02-hidden-runner-action-backup.json'
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'

function Assert-InteractiveAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'ADMINISTRATOR_REQUIRED'
    }
    if ((Get-Process -Id $PID).SessionId -eq 0) {
        throw 'INTERACTIVE_SESSION_REQUIRED'
    }
}

function Get-TaskAndGuard {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    if ($task.Principal.LogonType -ne 'Interactive') {
        throw "UNEXPECTED_LOGON_TYPE: $($task.Principal.LogonType)"
    }
    if ($task.Actions.Count -ne 1) {
        throw "UNEXPECTED_ACTION_COUNT: $($task.Actions.Count)"
    }
    $identityName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if ($task.Principal.UserId -ine $identityName) {
        throw "ACCOUNT_MISMATCH: task $($task.Principal.UserId); caller $identityName"
    }
    return $task
}

function New-OriginalAction {
    param([object]$Saved)
    return New-ScheduledTaskAction -Execute $Saved.execute -Argument $Saved.arguments -WorkingDirectory $Saved.working_directory
}

function Write-Status {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $action = $task.Actions[0]
    $isHidden = ($action.Execute -ieq $wscript) -and ($action.Arguments -like '*EF02-Hidden-Runner.vbs*')
    Write-Host "TASK=$taskName"
    Write-Host "TASK_STATE=$($task.State)"
    Write-Host "LOGON_TYPE=$($task.Principal.LogonType)"
    Write-Host "TASK_ACTION=$($action.Execute)"
    Write-Host "HIDDEN_ACTION_CONFIGURED=$isHidden"
    Write-Host "BACKUP_EXISTS=$(Test-Path -LiteralPath $backupFile)"
    Write-Host "RESTART_COUNT=$($task.Settings.RestartCount)"
    Write-Host "RESTART_INTERVAL=$($task.Settings.RestartInterval)"
    $svc = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if ($null -ne $svc) {
        Write-Host "SERVICE_STATUS=$($svc.State)"
        Write-Host "SERVICE_START_MODE=$($svc.StartMode)"
    }
    if ($isHidden) {
        $running = @(Get-CimInstance Win32_Process -Filter "Name='wscript.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*EF02-Hidden-Runner.vbs*' }).Count -gt 0
        Write-Host "HIDDEN_LAUNCHER_PROCESS=$running"
        if (-not $running) {
            Write-Host 'ACTIVATION=PENDING_NEXT_TASK_START_OR_LOGON'
        }
    }
}

if ($Mode -eq 'Status') {
    Write-Status
    return
}

Assert-InteractiveAdmin
$task = Get-TaskAndGuard
if (-not (Test-Path -LiteralPath $runnerCmd -PathType Leaf)) {
    throw "RUN_CMD_NOT_FOUND: $runnerCmd"
}
if (-not (Test-Path -LiteralPath $wscript -PathType Leaf)) {
    throw "WSCRIPT_NOT_FOUND: $wscript"
}
$action = $task.Actions[0]
$isHidden = ($action.Execute -ieq $wscript) -and ($action.Arguments -like '*EF02-Hidden-Runner.vbs*')

if ($Mode -eq 'Install') {
    if ($isHidden) {
        if (-not (Test-Path -LiteralPath $backupFile)) {
            throw 'HIDDEN_ACTION_WITHOUT_BACKUP: refusing to overwrite'
        }
        Write-Host 'HIDDEN_ACTION_ALREADY_CONFIGURED'
        Write-Status
        return
    }
    if (Test-Path -LiteralPath $backupFile) {
        throw 'BACKUP_ALREADY_EXISTS: inspect before retrying'
    }
    if (Test-Path -LiteralPath $launcher) {
        throw 'LAUNCHER_ALREADY_EXISTS: inspect before retrying'
    }
    if ($action.Execute -notlike '*cmd.exe' -or $action.Arguments -notlike '*run.cmd*') {
        throw 'UNEXPECTED_ORIGINAL_TASK_ACTION: refusing to modify unrelated task'
    }
    $svc = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if ($null -eq $svc -or $svc.State -ne 'Stopped' -or $svc.StartMode -ne 'Disabled') {
        throw 'SERVICE_NOT_STOPPED_DISABLED: refusing duplicate runner registration'
    }
    if ($task.Settings.RestartCount -lt 1) {
        throw 'TASK_RESTART_NOT_CONFIGURED: refusing to claim self-recovery'
    }
    $backup = [ordered]@{
        task_name = $taskName
        account = $task.Principal.UserId
        execute = $action.Execute
        arguments = $action.Arguments
        working_directory = $action.WorkingDirectory
        saved_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    # Use WSH's hidden style (0) and synchronous wait (True). Returning nonzero
    # ensures the existing Task Scheduler RestartOnFailure policy can relaunch.
    # This does NOT change the Windows login session, GitHub runner registration,
    # Codex sandbox, or the Antigravity command line.
    $vbs = @'
Option Explicit
Dim sh, runnerRoot, q, cmdLine, result
On Error Resume Next
Set sh = CreateObject("WScript.Shell")
If Err.Number <> 0 Then WScript.Quit 2
runnerRoot = "__RUNNER_ROOT__"
sh.CurrentDirectory = runnerRoot
If Err.Number <> 0 Then WScript.Quit 2
q = Chr(34)
cmdLine = q & sh.ExpandEnvironmentStrings("%ComSpec%") & q & " /d /c " & q & q & runnerRoot & "\run.cmd" & q & q
result = sh.Run(cmdLine, 0, True)
If Err.Number <> 0 Then WScript.Quit 2
' Force Task Scheduler's configured RestartOnFailure if the runner exits.
WScript.Quit 1
'@
    $vbs = $vbs.Replace('__RUNNER_ROOT__', $RunnerRoot)
    try {
        $vbs | Set-Content -LiteralPath $launcher -Encoding ASCII
        $backup | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $backupFile -Encoding UTF8
        $newAction = New-ScheduledTaskAction -Execute $wscript -Argument ('//B //Nologo "{0}"' -f $launcher) -WorkingDirectory $RunnerRoot
        Set-ScheduledTask -TaskName $taskName -Action $newAction -ErrorAction Stop | Out-Null
        Write-Host 'HIDDEN_RUNNER_ACTION_STAGED'
        Write-Host 'CURRENT_RUNNING_JOB_UNTOUCHED: this installer does not Stop-ScheduledTask.'
        Write-Host 'ACTIVATE_AT_NEXT_LOGON_OR_SAFE_TASK_RESTART'
        Write-Status
    } catch {
        $reason = $_
        try {
            Set-ScheduledTask -TaskName $taskName -Action (New-OriginalAction -Saved $backup) -ErrorAction Stop | Out-Null
            Remove-Item -LiteralPath $backupFile, $launcher -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Warning "ROLLBACK_INCOMPLETE: $($_.Exception.Message). Backup retained."
        }
        throw $reason
    }
    return
}

if ($Mode -eq 'Rollback') {
    if (-not (Test-Path -LiteralPath $backupFile -PathType Leaf)) {
        throw 'BACKUP_NOT_FOUND: cannot restore original action safely'
    }
    $saved = Get-Content -LiteralPath $backupFile -Raw | ConvertFrom-Json
    if ($saved.task_name -ne $taskName -or $saved.account -ine $task.Principal.UserId) {
        throw 'BACKUP_IDENTITY_MISMATCH'
    }
    if (-not $isHidden) {
        throw 'HIDDEN_ACTION_NOT_INSTALLED: refusing to overwrite current task action'
    }
    Set-ScheduledTask -TaskName $taskName -Action (New-OriginalAction -Saved $saved) -ErrorAction Stop | Out-Null
    Remove-Item -LiteralPath $backupFile -Force
    # The running WScript process has loaded its script; cleanup is best-effort.
    Remove-Item -LiteralPath $launcher -Force -ErrorAction SilentlyContinue
    Write-Host 'ORIGINAL_VISIBLE_ACTION_RESTORED'
    Write-Host 'CURRENT_RUNNING_JOB_UNTOUCHED: change applies on next task start.'
    Write-Status
}
