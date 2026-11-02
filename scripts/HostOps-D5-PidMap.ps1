#Requires -Version 7.0
<#
Read-only D5 PID ↔ CDP listener ↔ Chrome profile grouping.
No raw command lines, paths, credentials, or browser profile contents leave host.
The p01/p02 labels are per-run ephemeral and DO NOT prove the official D5 route.
No sockets are opened, processes launched, altered or terminated.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$pidLimit = 32
$listenerLimit = 16
$processKnown = $false
$tcpKnown = $false
$processes = @()
$listeners = @()
$chromeTotal = 0
$chromeTruncated = $false
$listenerTruncated = $false

$chromePids = [System.Collections.Generic.HashSet[int]]::new()
$profileGroups = [System.Collections.Generic.Dictionary[string, string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)

try {
    $chrome = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction Stop |
        Sort-Object -Property ProcessId)
    $processKnown = $true
    $chromeTotal = $chrome.Count
    $chromeTruncated = $chrome.Count -gt $pidLimit
    foreach ($item in $chrome) { $null = $chromePids.Add([int]$item.ProcessId) }
    foreach ($item in @($chrome | Select-Object -First $pidLimit)) {
        $raw = [string]$item.CommandLine
        $cmdKnown = -not [string]::IsNullOrWhiteSpace($raw)
        $debug9333 = $null
        $debug9222 = $null
        $profileGroup = $null
        $profileRootExists = $null

        if ($cmdKnown) {
            $debug9333 = [bool]($raw -match '(?i)(?:^|\s)--remote-debugging-port(?:=|\s+)9333(?=\s|$)')
            $debug9222 = [bool]($raw -match '(?i)(?:^|\s)--remote-debugging-port(?:=|\s+)9222(?=\s|$)')
            # Raw paths are used in memory ONLY to group identical profile claims.
            if ($raw -match '(?i)(?:^|\s)--user-data-dir(?:=|\s+)(?:"([^"]+)"|([^\s]+))') {
                $profilePath = $(if ($Matches[1]) { $Matches[1] } else { $Matches[2] })
                $profilePath = $profilePath.Trim('"').Trim("'").TrimEnd('\', '/')
                if (-not [string]::IsNullOrWhiteSpace($profilePath)) {
                    if (-not $profileGroups.ContainsKey($profilePath)) {
                        $profileGroups[$profilePath] = ('p{0:d2}' -f ($profileGroups.Count + 1))
                    }
                    $profileGroup = $profileGroups[$profilePath]
                    try {
                        $profileRootExists = [bool](Test-Path -LiteralPath $profilePath -PathType Container -ErrorAction Stop)
                    } catch {
                        $profileRootExists = $null
                    }
                }
            }
        }
        $processes += [ordered]@{
            pid = [int]$item.ProcessId
            session_id = [int]$item.SessionId
            commandline_known = [bool]$cmdKnown
            debug_9333 = $debug9333
            debug_9222 = $debug9222
            profile_group = $profileGroup
            profile_root_exists = $profileRootExists
        }
    }
} catch {
    $processKnown = $false
    $chromeTotal = 0
    $chromeTruncated = $false
    $processes = @()
}

try {
    $tcp = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalPort -eq 9333 -or $_.LocalPort -eq 9222 } |
        Sort-Object -Property LocalPort, OwningProcess)
    $tcpKnown = $true
    $listenerTruncated = $tcp.Count -gt $listenerLimit
    foreach ($entry in @($tcp | Select-Object -First $listenerLimit)) {
        $binding = 'other'
        if ([string]$entry.LocalAddress -eq '127.0.0.1') { $binding = 'ipv4_loopback' }
        elseif ([string]$entry.LocalAddress -eq '::1') { $binding = 'ipv6_loopback' }
        $listeners += [ordered]@{
            port = [int]$entry.LocalPort
            owner_pid = [int]$entry.OwningProcess
            binding = $binding
            chrome_owner = [bool]$chromePids.Contains([int]$entry.OwningProcess)
        }
    }
} catch {
    $tcpKnown = $false
    $listenerTruncated = $false
    $listeners = @()
}

$out = [ordered]@{
    operation = 'd5_pid_map_read'
    session_id = [int](Get-Process -Id $PID -ErrorAction Stop).SessionId
    pwsh_major = [int]$PSVersionTable.PSVersion.Major
    process_known = [bool]$processKnown
    tcp_known = [bool]$tcpKnown
    chrome_total = [int]$chromeTotal
    chrome_truncated = [bool]$chromeTruncated
    listener_truncated = [bool]$listenerTruncated
    profile_identity_verified = $false
    processes = @($processes)
    listeners = @($listeners)
}
$out | ConvertTo-Json -Compress -Depth 5
