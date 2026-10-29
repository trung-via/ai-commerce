#Requires -Version 5.1
<#
AI Commerce EF02 Windows runner: interactive Task Scheduler mode.
Run from Windows PowerShell as Administrator, logged in as the runner account.
Only controls actions.runner.trung-via-ai-commerce.AI-COMMERCE-EF02-01.
Modes: Install, Status, Rollback.
#>
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Status', 'Rollback')]
    [string]$Mode = 'Status',
    [string]$RunnerRoot = 'C:\TOOL\ai-commerce-runner'
)

$ErrorActionPreference = 'Stop'
$serviceName = 'actions.runner.trung-via-ai-commerce.AI-COMMERCE-EF02-01'
$taskName = 'AI-Commerce-EF02-Interactive'
$runnerCmd = Join-Path $RunnerRoot 'run.cmd'
$backupFile = Join-Path $RunnerRoot '.ef02-interactive-runner-backup.json'

function Assert-AdminInteractive {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'ADMINISTRATOR_REQUIRED: reopen Windows PowerShell with Run as administrator.'
    }
    if ((Get-Process -Id $PID).SessionId -eq 0) {
        throw 'INTERACTIVE_SESSION_REQUIRED: run this locally after Windows login, not inside a service or GitHub job.'
    }
}

function Get-ServiceInfo {
    $svc = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if ($null -eq $svc) { throw "AI_COMMERCE_RUNNER_SERVICE_NOT_FOUND: $serviceName" }
    return $svc
}

function Restore-FromBackup {
    param([object]$Backup)
    $startup = switch ($Backup.original_start_mode) {
        'Auto' { 'Automatic' }
        'Manual' { 'Manual' }
        'Disabled' { 'Disabled' }
        default { throw "UNKNOWN_BACKUP_START_MODE: $($Backup.original_start_mode)" }
    }
    Set-Service -Name $serviceName -StartupType $startup
    $current = Get-Service -Name $serviceName
    if ($Backup.original_was_running -and $current.Status -ne 'Running') {
        Start-Service -Name $serviceName -ErrorAction Stop
    }
}

function Print-Status {
    $svc = Get-ServiceInfo
    Write-Host "SERVICE_NAME=$serviceName"
    Write-Host "SERVICE_STATUS=$($svc.State)"
    Write-Host "SERVICE_START_MODE=$($svc.StartMode)"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host 'SCHEDULED_TASK=NOT_INSTALLED'
    } else {
        Write-Host "SCHEDULED_TASK=$taskName"
        Write-Host "SCHEDULED_TASK_STATE=$($task.State)"
        Write-Host "TASK_LOGON_TYPE=$($task.Principal.LogonType)"
        Write-Host "TASK_USER=$($task.Principal.UserId)"
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        Write-Host "LAST_TASK_RESULT=$($info.LastTaskResult)"
    }
    Write-Host "BACKUP_EXISTS=$(Test-Path $backupFile)"
}

if ($Mode -eq 'Status') {
    Print-Status
    return
}

Assert-AdminInteractive
$svc = Get-ServiceInfo
$identityName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$serviceUser = $svc.StartName
if ($serviceUser.StartsWith('.\')) {
    $serviceUser = $env:COMPUTERNAME + '\' + $serviceUser.Substring(2)
}
if ($serviceUser -ine $identityName) {
    throw "ACCOUNT_MISMATCH: task user $identityName differs from runner service account $serviceUser."
}
if (-not (Test-Path -LiteralPath $runnerCmd -PathType Leaf)) {
    throw "RUN_CMD_NOT_FOUND: $runnerCmd"
}

if ($Mode -eq 'Install') {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Write-Host 'TASK_ALREADY_REGISTERED: no duplicate task created.'
        Print-Status
        return
    }
    if (Test-Path $backupFile) {
        throw "PREVIOUS_BACKUP_EXISTS: inspect $backupFile before installing."
    }
    $backup = [ordered]@{
        service_name = $serviceName
        task_name = $taskName
        original_start_mode = $svc.StartMode
        original_was_running = ($svc.State -eq 'Running')
        account = $identityName
        created_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $backup | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $backupFile -Encoding UTF8
    $registered = $false
    try {
        $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument ('/d /c ""{0}""' -f $runnerCmd) -WorkingDirectory $RunnerRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identityName
        $taskPrincipal = New-ScheduledTaskPrincipal -UserId $identityName -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $settings -Description 'AI Commerce EF02 runner in interactive Windows session; Codex MXC; not a Windows service' | Out-Null
        $registered = $true

        # Prevent the same GitHub runner registration from being active twice.
        Stop-Service -Name $serviceName -ErrorAction Stop
        Set-Service -Name $serviceName -StartupType Disabled
        Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Start-Sleep -Seconds 5
        $task = Get-ScheduledTask -TaskName $taskName
        if ($task.State -ne 'Running') {
            throw "INTERACTIVE_TASK_NOT_RUNNING: observed $($task.State)."
        }
        Write-Host 'INTERACTIVE_RUNNER_TASK_RUNNING'
        Write-Host 'Windows sign-in is required; runner stops when user signs out.'
        Write-Host 'Next verification: GitHub EF02 Codex MSI Shell Probe, confirm SESSION_ID > 0 and mxc_msi/mxc_pwsh_alias PASS.'
        Print-Status
    } catch {
        $failure = $_
        Write-Warning "INSTALL_FAILED: $($failure.Exception.Message). Attempting to restore original service."
        if ($registered) {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        }
        try {
            Restore-FromBackup -Backup $backup
            Remove-Item -LiteralPath $backupFile -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Warning "ROLLBACK_INCOMPLETE: $($_.Exception.Message). Backup retained at $backupFile."
        }
        throw $failure
    }
    return
}

if ($Mode -eq 'Rollback') {
    if (-not (Test-Path $backupFile)) {
        throw "BACKUP_NOT_FOUND: refusal to guess original service start mode. $backupFile"
    }
    $backup = Get-Content -LiteralPath $backupFile -Raw | ConvertFrom-Json
    if ($backup.service_name -ne $serviceName -or $backup.task_name -ne $taskName) {
        throw 'BACKUP_IDENTITY_MISMATCH'
    }
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Restore-FromBackup -Backup $backup
    Remove-Item -LiteralPath $backupFile -Force
    Write-Host 'RESTORED_ORIGINAL_AI_COMMERCE_SERVICE'
    Print-Status
}
