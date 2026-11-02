# HostOps v4 Windows INSTALL PLAN ONLY. Does not install, sign or elevate.
# Run: powershell -NoProfile -File .\HostOps-V4-InstallPlan.ps1 -Plan [-ConfigPath ...]
[CmdletBinding()]
param(
    [switch]$Plan,
    [string]$ConfigPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $Plan) {
    Write-Error 'INSTALL_DISABLED_USE_EXPLICIT_PLAN'
    exit 2
}

$state = [ordered]@{
    schema = 'hostops.v4.windows_install_plan.v1'
    stage = 'READ_ONLY_PLAN_NO_CHANGES'
    windows = ($env:OS -eq 'Windows_NT')
    caller_non_elevated = $false
    config_present = $false
    config_schema_valid = $false
    config_disabled = $false
    source_public_control_private_declared = $false
    distinct_non_admin_sid_configured = $false
    release_dir_present = $false
    release_manifest_present = $false
    release_signature_present = $false
    policy_dir_present = $false
    human_public_signers_present = $false
    issuer_public_signers_present = $false
    ipc_key_present = $false
    revocations_present = $false
    kill_marker_present = $false
    broker_state_dir_present = $false
    transport_state_dir_present = $false
    native_acl_verified = $false
    native_two_account_ipc_verified = $false
    human_release_signature_verified = $false
    private_control_github_verified = $false
    ready_to_enable = $false
    block_reasons = @()
}
$reasons = [System.Collections.Generic.List[string]]::new()
if (-not $state.windows) {
    $reasons.Add('WINDOWS_REQUIRED')
} else {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        $state.caller_non_elevated = -not $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { $reasons.Add('WINDOWS_IDENTITY_UNAVAILABLE') }
}
if ($ConfigPath -eq '') {
    $ConfigPath = Join-Path $PSScriptRoot '..\config\hostops-v3\config-v4.example.json'
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    $reasons.Add('CONFIG_ABSENT')
} else {
    $state.config_present = $true
    try {
        $raw = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
        if ($raw.Length -gt 131072) { throw 'CONFIG_TOO_LARGE' }
        $config = $raw | ConvertFrom-Json -ErrorAction Stop
        $required = @('schema', 'enabled', 'host_id', 'source_repository',
            'control_repository', 'policy_dir', 'state_dir', 'transport_state_dir',
            'release_dir', 'broker_identity', 'transport_identity',
            'untrusted_identities', 'trusted_owners', 'endpoint',
            'ssh_keygen', 'ssh_keygen_sha256', 'pwsh', 'pwsh_sha256')
        $actual = @($config.PSObject.Properties.Name)
        $missing = @($required | Where-Object { $actual -cnotcontains $_ })
        $extras = @($actual | Where-Object { $required -cnotcontains $_ })
        if ($missing.Count -ne 0 -or $extras.Count -ne 0 -or
            $config.schema -cne 'hostops.config.v4') { throw 'CONFIG_SCHEMA_INVALID' }
        $state.config_schema_valid = $true
        $state.config_disabled = ($config.enabled -is [bool] -and $config.enabled -eq $false)
        $state.source_public_control_private_declared = (
            $config.source_repository -ceq 'trung-via/ai-commerce' -and
            $config.control_repository -ceq 'trung-via/ai-commerce-hostops-control')
        $sidPattern = '^S-1-[0-9]+(-[0-9]+){2,}$'
        $a = [string]$config.broker_identity
        $b = [string]$config.transport_identity
        $state.distinct_non_admin_sid_configured = (
            $a -match $sidPattern -and $b -match $sidPattern -and
            $a -cne $b -and
            @('S-1-5-18','S-1-5-19','S-1-5-20','S-1-5-32-544') -cnotcontains $a -and
            @('S-1-5-18','S-1-5-19','S-1-5-20','S-1-5-32-544') -cnotcontains $b)
        function HasLocalItem([string]$path, [string]$kind) {
            if ([string]::IsNullOrWhiteSpace($path) -or
                $path.StartsWith('\\') -or
                -not [IO.Path]::IsPathRooted($path)) { return $false }
            return (Test-Path -LiteralPath $path -PathType $kind)
        }
        $state.release_dir_present = HasLocalItem ([string]$config.release_dir) 'Container'
        $state.release_manifest_present = HasLocalItem (
            Join-Path ([string]$config.release_dir) 'release.json') 'Leaf'
        $state.release_signature_present = HasLocalItem (
            Join-Path ([string]$config.release_dir) 'release.sig.b64') 'Leaf'
        $state.policy_dir_present = HasLocalItem ([string]$config.policy_dir) 'Container'
        $state.human_public_signers_present = HasLocalItem (
            Join-Path ([string]$config.policy_dir) 'human-signers') 'Leaf'
        $state.issuer_public_signers_present = HasLocalItem (
            Join-Path ([string]$config.policy_dir) 'issuer-signers') 'Leaf'
        $state.ipc_key_present = HasLocalItem (
            Join-Path ([string]$config.policy_dir) 'ipc.key') 'Leaf'
        $state.revocations_present = HasLocalItem (
            Join-Path ([string]$config.policy_dir) 'revocations.json') 'Leaf'
        $state.kill_marker_present = HasLocalItem (
            Join-Path ([string]$config.policy_dir) 'KILL') 'Leaf'
        $state.broker_state_dir_present = HasLocalItem ([string]$config.state_dir) 'Container'
        $state.transport_state_dir_present = HasLocalItem (
            ([string]$config.transport_state_dir)) 'Container'
    } catch {
        $state.config_schema_valid = $false
        $reasons.Add('CONFIG_INVALID_OR_UNREADABLE')
    }
}
if (-not $state.config_disabled) { $reasons.Add('CONFIG_NOT_PROVEN_DISABLED') }
if (-not $state.source_public_control_private_declared) { $reasons.Add('REPOSITORY_BINDING_UNPROVEN') }
if (-not $state.distinct_non_admin_sid_configured) { $reasons.Add('DISTINCT_SID_UNPROVEN') }
if (-not $state.kill_marker_present) { $reasons.Add('KILL_MARKER_NOT_PROVEN') }
if (-not $state.release_signature_present) { $reasons.Add('SIGNED_RELEASE_MISSING') }
$reasons.Add('NATIVE_ACL_AND_TWO_ACCOUNT_IPC_NOT_VERIFIED')
$reasons.Add('PRIVATE_CONTROL_PROVENANCE_NOT_VERIFIED')
$state.block_reasons = @($reasons)
# Intentionally no enable/install/apply mode; absence of independent proof is HOLD.
$state | ConvertTo-Json -Depth 5
