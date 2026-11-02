#Requires -Version 7.0
<# Fixed, digest-pinned v3 script. No command text, file paths, process or environment export. #>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$nonElevated = $true
if ($IsWindows) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $nonElevated = -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
[ordered]@{
    pwsh_major = [int]$PSVersionTable.PSVersion.Major
    non_elevated = [bool]$nonElevated
} | ConvertTo-Json -Compress
