#Requires -Version 5.1
<# Private enrolled D5 adapter, NOT a public Issue command entrypoint.
No default enrollment; Native requires a short-lived MAC from the private bridge.
Logon sends one fixed trigger to the enrolled private receiver. No secrets in task.
Same-user public runner access makes this deployment inadmissible (see handoff).
#>
[CmdletBinding()]
param([switch]$Logon)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$changed = $false
$result = @{code='OK'}
function Deny([string]$code) { throw $code }
function Hash-Bytes([byte[]]$bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Hash-File([string]$path) { return Hash-Bytes ([IO.File]::ReadAllBytes($path)) }
function No-Links([string]$path) {
    $item = Get-Item -LiteralPath $path -Force
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Deny 'OWNERSHIP_UNPROVEN' }
        $parent = [IO.Path]::GetDirectoryName($item.FullName)
        if (-not $parent -or $parent -eq $item.FullName) { break }
        $item = Get-Item -LiteralPath $parent -Force
    }
}
function Private-Path([string]$path) {
    No-Links $path
    $acl = Get-Acl -LiteralPath $path
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid) { Deny 'OWNERSHIP_UNPROVEN' }
    $ownerAce = $false
    foreach ($ace in $acl.Access) {
        $who = $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($ace.AccessControlType -eq 'Allow') {
            if ($who -notin @($sid,'S-1-5-18','S-1-5-32-544')) { Deny 'OWNERSHIP_UNPROVEN' }
            if ($who -eq $sid) { $ownerAce = $true }
        }
    }
    if (-not $ownerAce) { Deny 'OWNERSHIP_UNPROVEN' }
}
function Save-New([string]$path, [byte[]]$bytes) {
    $stream = [IO.File]::Open($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
function Task-Xml([string]$name) {
    return [string](Export-ScheduledTask -TaskName $name -TaskPath '\')
}
function Task-Hash([string]$name) { return Hash-Bytes ([Text.Encoding]::UTF8.GetBytes((Task-Xml $name))) }
function Task-Exists([string]$name) {
    return $null -ne (Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue)
}
function Check-Tasks {
    if (Test-Path -LiteralPath $taskPin) { Private-Path $taskPin }
    if (Test-Path -LiteralPath $launcher) { Private-Path $launcher }
    foreach ($name in $p.runner_tasks) {
        if (-not (Task-Exists $name)) { Deny 'RUNNER_PRESERVATION_UNPROVEN' }
        if ($runnerBefore[$name] -ne (Task-Hash $name)) { Deny 'RUNNER_PRESERVATION_UNPROVEN' }
    }
    $all = @(Get-ScheduledTask | Where-Object { $_.TaskName -like 'AI-Commerce-D5-*' })
    foreach ($task in $all) {
        if ($task.TaskPath -ne '\') { Deny 'TASK_OWNERSHIP_UNPROVEN' }
        if ($task.TaskName -eq $taskName) {
            if (-not (Test-Path -LiteralPath $taskPin) -or (Task-Hash $taskName) -ne [IO.File]::ReadAllText($taskPin)) {
                Deny 'TASK_OWNERSHIP_UNPROVEN'
            }
        } else {
            $known = @($p.legacy_tasks | Where-Object { $_.name -ceq $task.TaskName })
            if ($known.Count -ne 1 -or (Task-Hash $task.TaskName) -ne $known[0].xml_sha256) {
                Deny 'TASK_OWNERSHIP_UNPROVEN'
            }
            if ($task.State -eq 'Running') { Deny 'UNKNOWN_EFFECT_HOLD' }
        }
    }
    foreach ($old in $p.legacy_tasks) {
        if (Test-Path -LiteralPath $old.launcher) {
            Private-Path $old.launcher
            if ((Hash-File $old.launcher) -ne $old.sha256) { Deny 'LAUNCHER_UNVERIFIED' }
        }
    }
}
function No-Conflicts {
    foreach ($old in $p.legacy_tasks) {
        if ((Task-Exists $old.name) -or (Test-Path -LiteralPath $old.launcher)) { Deny 'CONFLICTING_D5_TASK' }
    }
}
function Check-Root([int]$targetPid, [string]$created) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid"
    if ($null -eq $proc) { Deny 'OWNERSHIP_UNPROVEN' }
    $owner = Invoke-CimMethod -InputObject $proc -MethodName GetOwnerSid
    if ($owner.Sid -ne $sid -or $proc.SessionId -ne $session -or
        $proc.CreationDate.ToUniversalTime().ToString('o') -cne $created -or
        $proc.ExecutablePath -ine $cfg.executable) { Deny 'OWNERSHIP_UNPROVEN' }
    $argsFound = [D5Argv]::Parse($proc.CommandLine)
    foreach ($pair in @(@('--user-data-dir',$cfg.user_data_dir), @('--profile-directory',$cfg.profile_directory),
                        @('--remote-debugging-port','9333'), @('--remote-debugging-address','127.0.0.1'))) {
        $values = @($argsFound | Where-Object { $_.StartsWith($pair[0]+'=') })
        if ($values.Count -ne 1 -or $values[0].Substring($pair[0].Length+1) -ine $pair[1]) { Deny 'OWNERSHIP_UNPROVEN' }
    }
    if (@($argsFound | Where-Object { $_ -like '--type=*' }).Count -ne 0) { Deny 'OWNERSHIP_UNPROVEN' }
    return $proc
}
function Assert-Absent {
    if (@(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 9333).Count) { Deny 'OWNERSHIP_UNPROVEN' }
    foreach ($chrome in @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'")) {
        if (-not $chrome.CommandLine) { Deny 'OWNERSHIP_UNPROVEN' }
        $argsFound = [D5Argv]::Parse($chrome.CommandLine)
        $profileArgs = @($argsFound | Where-Object { $_ -ilike '--user-data-dir*' })
        if ($profileArgs.Count) {
            if ($profileArgs.Count -ne 1 -or -not $profileArgs[0].StartsWith('--user-data-dir=')) { Deny 'OWNERSHIP_UNPROVEN' }
            $pathArg = [IO.Path]::GetFullPath($profileArgs[0].Substring(16)).TrimEnd('\')
            if ($pathArg -ieq ([IO.Path]::GetFullPath($cfg.user_data_dir).TrimEnd('\'))) { Deny 'PROFILE_ALREADY_ACTIVE_NO_CDP' }
        }
    }
}
function Check-Only-Owned-Root([int]$targetPid) {
    $listeners = @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 9333)
    if ($listeners.Count -gt 1) { Deny 'OWNERSHIP_UNPROVEN' }
    if ($listeners.Count -eq 1 -and
        ($listeners[0].LocalAddress -cne '127.0.0.1' -or $listeners[0].OwningProcess -ne $targetPid)) { Deny 'OWNERSHIP_UNPROVEN' }
    $roots = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'")
    if ($roots.Count -gt 256) { Deny 'OWNERSHIP_UNPROVEN' }
    $claims = 0
    foreach ($root in $roots) {
        if (-not $root.CommandLine) { Deny 'OWNERSHIP_UNPROVEN' }
        $argv = [D5Argv]::Parse($root.CommandLine)
        if (@($argv | Where-Object { $_ -like '--type=*' }).Count) { continue }
        $profileArgs = @($argv | Where-Object { $_ -ilike '--user-data-dir*' })
        if ($profileArgs.Count) {
            if ($profileArgs.Count -ne 1 -or -not $profileArgs[0].StartsWith('--user-data-dir=')) { Deny 'OWNERSHIP_UNPROVEN' }
            if ([IO.Path]::GetFullPath($profileArgs[0].Substring(16)).TrimEnd('\') -ieq
                [IO.Path]::GetFullPath($cfg.user_data_dir).TrimEnd('\')) {
                $claims++
                if ($root.ProcessId -ne $targetPid) { Deny 'OWNERSHIP_UNPROVEN' }
            }
        }
    }
    if ($claims -ne 1) { Deny 'OWNERSHIP_UNPROVEN' }
}
try {
    if ($env:GITHUB_ACTIONS -eq 'true' -or $env:OS -ne 'Windows_NT') { Deny 'UNSUPPORTED_SESSION' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = $identity.User.Value
    $session = [Diagnostics.Process]::GetCurrentProcess().SessionId
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($session -eq 0 -or $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Deny 'UNSUPPORTED_SESSION' }
    $state = Join-Path $env:LOCALAPPDATA 'AI-Commerce\D5-Operator'
    Private-Path $state
    if ($Logon) {
        # The private receiver authenticates native client SID/session, reloads
        # signed enrollment/revocation and calls Bridge.logon(). Never Wake.
        $pipe = [IO.Pipes.NamedPipeClientStream]::new('.', 'AI-Commerce-D5-Operator-v1',
            [IO.Pipes.PipeDirection]::InOut, [IO.Pipes.PipeOptions]::Asynchronous,
            [Security.Principal.TokenImpersonationLevel]::Impersonation)
        try {
            $pipe.Connect(2000)
            $pipe.ReadMode = [IO.Pipes.PipeTransmissionMode]::Message
            $bytes = [Text.Encoding]::UTF8.GetBytes("D5_LOGON_START_V1`n")
            $pipe.Write($bytes,0,$bytes.Length); $pipe.Flush()
            $reply = New-Object byte[] 8192
            $read = $pipe.ReadAsync($reply,0,$reply.Length)
            if (-not $read.Wait(55000) -or $read.Result -le 0 -or -not $pipe.IsMessageComplete) { Deny 'UNKNOWN_EFFECT_HOLD' }
            $ack = [Text.Encoding]::UTF8.GetString($reply,0,$read.Result) | ConvertFrom-Json
            if ($ack.code -cne 'LOGON_TRIGGER_DELIVERED' -or $ack.status -cne 'OBSERVED') { Deny 'UNKNOWN_EFFECT_HOLD' }
        } finally { $pipe.Dispose() }
        # Trigger delivery is NOT evidence of browser startup.
        Write-Output '{"code":"LOGON_TRIGGER_DELIVERED"}'
        exit 0
    }
    Private-Path (Join-Path $state 'control.key')
    $inputBuffer = New-Object char[] 65537
    $inputCount = [Console]::In.ReadBlock($inputBuffer,0,$inputBuffer.Length)
    if ($inputCount -gt 65536) { Deny 'OWNERSHIP_UNPROVEN' }
    $text = [string]::new($inputBuffer,0,$inputCount)
    $envelope = $text | ConvertFrom-Json
    if (@($envelope.PSObject.Properties.Name).Count -ne 2) { Deny 'OWNERSHIP_UNPROVEN' }
    $body = [Convert]::FromBase64String($envelope.body)
    $mac = [Security.Cryptography.HMACSHA256]::new([IO.File]::ReadAllBytes((Join-Path $state 'control.key')))
    try { $expected = ([BitConverter]::ToString($mac.ComputeHash($body))).Replace('-','').ToLowerInvariant() }
    finally { $mac.Dispose() }
    if ($envelope.mac -cne $expected) { Deny 'OWNERSHIP_UNPROVEN' }
    $p = [Text.Encoding]::UTF8.GetString($body) | ConvertFrom-Json
    # A local MAC is never evidence of independent custody. Revalidate the
    # signed grant/lease through the fixed protected isolated runtime first.
    $runtime = Join-Path $env:ProgramFiles 'AI-Commerce\D5-Runtime\python.exe'
    No-Links $runtime
    foreach ($trustedPath in @($runtime,[IO.Path]::GetDirectoryName($runtime))) {
        $trustedAcl = Get-Acl -LiteralPath $trustedPath
        $trustedSids = @('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
        if ($trustedAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trustedSids) { Deny 'BLOCKED_NOT_DEPLOYED' }
        foreach ($ace in $trustedAcl.Access) {
            if ($ace.AccessControlType -eq 'Allow' -and
                ($ace.FileSystemRights -band 0x500D0156) -ne 0 -and
                $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $trustedSids) { Deny 'BLOCKED_NOT_DEPLOYED' }
        }
    }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $runtime
    $start.Arguments = '-I -B -m ai_commerce.hostops_v3.d5_control --native-preflight'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $guard = [Diagnostics.Process]::Start($start)
    try {
        $guard.StandardInput.Write([Text.Encoding]::UTF8.GetString($body)); $guard.StandardInput.Close()
        if (-not $guard.WaitForExit(15000)) { Deny 'BLOCKED_NOT_DEPLOYED' }
        $guardBuffer = New-Object char[] 128
        $guardCount = $guard.StandardOutput.ReadBlock($guardBuffer,0,$guardBuffer.Length)
        $guardText = [string]::new($guardBuffer,0,$guardCount)
        if ($guard.ExitCode -ne 0 -or $guardCount -ge 128 -or $guardText.Trim() -cne '{"code":"OK"}') { Deny 'BLOCKED_NOT_DEPLOYED' }
    } finally { $guard.Dispose() }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($p.sid -cne $sid -or $p.state -ine $state -or $p.nonce -cnotmatch '^[0-9a-f]{32}$' -or
        $p.request_id -cnotmatch '^[0-9a-f]{32}$' -or $p.expires -le $now -or $p.expires -gt ($now+30)) { Deny 'OWNERSHIP_UNPROVEN' }
    foreach ($file in $p.files.PSObject.Properties) {
        if ($file.Name -match '(^/|\\|:|(^|/)\.\.(/|$))') { Deny 'SOURCE_DRIFT' }
        $target = Join-Path $p.root $file.Name
        No-Links $target
        if ((Hash-File $target) -cne $file.Value) { Deny 'SOURCE_DRIFT' }
    }
    $sourceName = 'scripts/AI-Commerce-D5-Operator-Bridge.ps1'
    if ((Hash-File $PSCommandPath) -cne $p.files.$sourceName) { Deny 'SOURCE_DRIFT' }
    Save-New (Join-Path $state ($p.nonce+'.nonce')) $body # reserve native call, replay/crash fails closed
    $cfg = Get-Content -LiteralPath $p.config -Raw | ConvertFrom-Json
    Private-Path $p.config
    Private-Path $cfg.user_data_dir
    if ($cfg.executable -ine $p.chrome.executable -or $cfg.user_data_dir -ine $p.chrome.user_data_dir -or
        $cfg.profile_directory -cne $p.chrome.profile_directory -or
        $cfg.cdp_endpoint.TrimEnd('/') -cne 'http://127.0.0.1:9333' -or
        $cfg.exclusive_user_data -ne $true) { Deny 'OWNERSHIP_UNPROVEN' }
    $signature = Get-AuthenticodeSignature -LiteralPath $cfg.executable
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|, )CN=Google (LLC|Inc)(,|$)') { Deny 'OWNERSHIP_UNPROVEN' }
    $taskName = 'AI-Commerce-D5-Operator'
    $taskPin = Join-Path $state 'task.sha256'
    $launcher = Join-Path $state 'launcher.ps1'
    $runnerBefore = @{}
    if (@($p.runner_tasks).Count -ne 3 -or @($p.runner_tasks | Select-Object -Unique).Count -ne 3) { Deny 'RUNNER_PRESERVATION_UNPROVEN' }
    $requiredRunners = @('AI-Commerce-EF02-Interactive','AI-Commerce-EF02-02-Interactive','AI-Commerce-HostOps-01-Interactive')
    if (@(Compare-Object $requiredRunners @($p.runner_tasks)).Count -ne 0) { Deny 'RUNNER_PRESERVATION_UNPROVEN' }
    foreach ($name in $p.runner_tasks) {
        if ($name -eq $taskName -or -not (Task-Exists $name)) { Deny 'RUNNER_PRESERVATION_UNPROVEN' }
        $runnerBefore[$name] = Task-Hash $name
    }
    foreach ($old in $p.legacy_tasks) {
        if ($old.name -notlike 'AI-Commerce-D5-*' -or $old.name -eq $taskName -or $old.name -in $p.runner_tasks -or
            [IO.Path]::GetDirectoryName($old.launcher) -ine (Join-Path $env:LOCALAPPDATA 'AI-Commerce\D5-Autostart') -or
            [IO.Path]::GetExtension($old.launcher) -ine '.ps1') { Deny 'OWNERSHIP_UNPROVEN' }
    }
    Check-Tasks
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class D5Argv {
 [DllImport("shell32.dll", SetLastError=true)] static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string s, out int n);
 [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
 public static string[] Parse(string s) {
  int n; IntPtr p=CommandLineToArgvW(s,out n);
  if(p==IntPtr.Zero || n<1 || n>256) throw new Exception();
  try { var a=new string[n]; for(int i=0;i<n;i++) a[i]=Marshal.PtrToStringUni(Marshal.ReadIntPtr(p,i*IntPtr.Size)); return a; }
  finally { LocalFree(p); }
 }
}
public static class D5Desktop {
 [DllImport("user32.dll", SetLastError=true)] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr handle);
 public static bool Available() { var h=OpenInputDesktop(0,false,1); if(h==IntPtr.Zero) return false; CloseDesktop(h); return true; }
}
'@
    if (-not [D5Desktop]::Available()) { Deny 'UNSUPPORTED_SESSION' }
    if ($p.op -in @('RETIRE_NEW_AUTOSTART','INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER','INSTALL_SINGLE_LOGON_AUTOSTART','REMOVE_D5_AUTOSTART','ROLLBACK')) {
        Assert-Absent # do not trust the earlier Python observation across process boundaries
    }
    switch ($p.op) {
        'MIGRATION_PREFLIGHT' { }
        'CHECK_NO_CONFLICTS' { No-Conflicts }
        'RETIRE_NEW_AUTOSTART' {
            # Versioned local metadata only. Never export secrets or profile files.
            $backup = Join-Path $state ('backup-'+$p.request_id)
            if (Test-Path -LiteralPath $backup) { Deny 'BACKUP_UNVERIFIED' }
            [IO.Directory]::CreateDirectory($backup) | Out-Null
            foreach ($old in $p.legacy_tasks) {
                if (Task-Exists $old.name) {
                    Save-New (Join-Path $backup ($old.name+'.xml')) ([Text.Encoding]::UTF8.GetBytes((Task-Xml $old.name)))
                    if ((Hash-File (Join-Path $backup ($old.name+'.xml'))) -cne $old.xml_sha256) { Deny 'BACKUP_UNVERIFIED' }
                }
                if (Test-Path -LiteralPath $old.launcher) {
                    Save-New (Join-Path $backup ($old.name+'.ps1')) ([IO.File]::ReadAllBytes($old.launcher))
                    if ((Hash-File (Join-Path $backup ($old.name+'.ps1'))) -cne $old.sha256) { Deny 'BACKUP_UNVERIFIED' }
                }
            }
            Check-Tasks # recheck ownership immediately before mutation
            $changed = $true
            foreach ($old in $p.legacy_tasks) {
                if (Task-Exists $old.name) { Unregister-ScheduledTask -TaskName $old.name -TaskPath '\' -Confirm:$false }
                if (Test-Path -LiteralPath $old.launcher) { Remove-Item -LiteralPath $old.launcher }
            }
            No-Conflicts
        }
        'STOP_OWNED_D5_CHROME' {
            $null = Check-Root $p.snapshot.pid $p.snapshot.created
            $process = [Diagnostics.Process]::GetProcessById($p.snapshot.pid)
            try {
                $null = $process.Handle # pin the process object before rechecking PID/creation
                $null = Check-Root $p.snapshot.pid $p.snapshot.created
                Check-Only-Owned-Root $p.snapshot.pid
                $changed = $true
                if (-not $process.CloseMainWindow() -or -not $process.WaitForExit(10000)) { Deny 'UNKNOWN_EFFECT_HOLD' }
                if ($null -ne (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.snapshot.pid)")) { Deny 'UNKNOWN_EFFECT_HOLD' }
            } finally { $process.Dispose() }
        }
        'INSTALL_VERIFIED_OLD_BEHAVIOR_LAUNCHER' {
            No-Conflicts
            if (Test-Path -LiteralPath $launcher) {
                if ((Hash-File $launcher) -cne $p.files.$sourceName) { Deny 'LAUNCHER_UNVERIFIED' }
            } else {
                $changed = $true
                Save-New $launcher ([IO.File]::ReadAllBytes((Join-Path $p.root $sourceName)))
            }
        }
        'START_OR_REUSE_D5_CHROME' {
            No-Conflicts
            # Independently reject a profile claim and every port listener at the
            # last boundary. Only the bridge's single new process may stabilize.
            Assert-Absent
            if ($cfg.allow_launch -ne $true) { Deny 'OWNERSHIP_UNPROVEN' }
            foreach ($lockName in @('SingletonLock','SingletonCookie','SingletonSocket','lockfile')) {
                if (Test-Path -LiteralPath (Join-Path $cfg.user_data_dir $lockName)) { Deny 'PROFILE_LOCKED' }
            }
            $info = [Diagnostics.ProcessStartInfo]::new()
            $info.FileName = $cfg.executable
            $info.UseShellExecute = $false
            $info.Arguments = '"--user-data-dir='+$cfg.user_data_dir+'" "--profile-directory='+$cfg.profile_directory+'" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9333 --no-first-run --no-default-browser-check'
            $changed = $true
            $child = [Diagnostics.Process]::Start($info)
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.Id)"
            $created = $proc.CreationDate.ToUniversalTime().ToString('o')
            $null = Check-Root $child.Id $created
            $result = @{code='OK'; pid=$child.Id; created=$created}
        }
        'INSTALL_SINGLE_LOGON_AUTOSTART' {
            No-Conflicts
            if (-not (Test-Path -LiteralPath $launcher) -or (Hash-File $launcher) -cne $p.files.$sourceName) { Deny 'LAUNCHER_UNVERIFIED' }
            if (-not (Task-Exists $taskName)) {
                if (Test-Path -LiteralPath $taskPin) { Deny 'TASK_OWNERSHIP_UNPROVEN' }
                $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
                $action = New-ScheduledTaskAction -Execute $shell -Argument ('-NoProfile -NonInteractive -File "'+$launcher+'" -Logon')
                $trigger = New-ScheduledTaskTrigger -AtLogOn -User $sid
                $user = New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
                $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
                $changed = $true
                Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $user -Settings $settings | Out-Null
                Save-New $taskPin ([Text.Encoding]::ASCII.GetBytes((Task-Hash $taskName)))
            }
        }
        { $_ -in @('REMOVE_D5_AUTOSTART','ROLLBACK') } {
            Check-Tasks
            $changed = $true
            if (Task-Exists $taskName) { Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false }
            if (Test-Path -LiteralPath $taskPin) { Remove-Item -LiteralPath $taskPin }
            if ($p.op -eq 'ROLLBACK' -and (Test-Path -LiteralPath $launcher)) {
                if ((Hash-File $launcher) -cne $p.files.$sourceName) { Deny 'LAUNCHER_UNVERIFIED' }
                Remove-Item -LiteralPath $launcher
            }
            # Rollback is disabled baseline, NEVER restore the unproven chain.
            if (Task-Exists $taskName) { Deny 'UNKNOWN_EFFECT_HOLD' }
        }
        default { Deny 'OWNERSHIP_UNPROVEN' }
    }
    Check-Tasks
    # Native durable receipt is local, independently readable, and correlated
    # with the bridge intent. A public response contains no paths or process data.
    $runnerAfter = @{}
    foreach ($name in $p.runner_tasks) { $runnerAfter[$name] = Task-Hash $name }
    $audit = @{request_id=$p.request_id; operation=$p.op; source_sha=$p.source_sha; code='OK'; native=$true;
        sid=$sid; session=$session; runner_before=$runnerBefore; runner_after=$runnerAfter;
        process_before=$p.snapshot; process_result=$result; registered=(Task-Exists $taskName);
        observed_utc=[DateTime]::UtcNow.ToString('o')}
    Save-New (Join-Path $state ($p.nonce+'.receipt.json')) ([Text.Encoding]::UTF8.GetBytes(($audit | ConvertTo-Json -Compress)))
    $result | ConvertTo-Json -Compress
    exit 0
} catch {
    # Never emit a stack trace, native stderr, private path or arbitrary message.
    $code = 'NATIVE_FAILURE_HOLD'
    if ($changed) { $code = 'UNKNOWN_EFFECT_HOLD' }
    elseif ($_.Exception.Message -cin @('BLOCKED_NOT_DEPLOYED','UNSUPPORTED_SESSION','OWNERSHIP_UNPROVEN','SOURCE_DRIFT','RUNNER_PRESERVATION_UNPROVEN','TASK_OWNERSHIP_UNPROVEN','CONFLICTING_D5_TASK','LAUNCHER_UNVERIFIED','PROFILE_ALREADY_ACTIVE_NO_CDP','PROFILE_LOCKED','BACKUP_UNVERIFIED','UNKNOWN_EFFECT_HOLD')) { $code=$_.Exception.Message }
    @{code=$code} | ConvertTo-Json -Compress
    exit 2
}
