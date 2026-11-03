# HostOps v4 Windows INSTALL PLAN ONLY. Does not install, sign or elevate.
# Run: pwsh -NoLogo -NoProfile -NonInteractive -File .\HostOps-V4-InstallPlan.ps1 -Plan [-ConfigPath ...]
[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$Plan,
    [switch]$NativeReadOnly,
    [string]$ConfigPath = '',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UnsupportedArgs = @()
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $Plan -or $UnsupportedArgs.Count -gt 0) {
    Write-Error 'INSTALL_DISABLED_USE_EXPLICIT_PLAN'
    exit 2
}

$runtimeEdition = [string]$PSVersionTable.PSEdition
$runtimeMajor = [int]$PSVersionTable.PSVersion.Major
$runtimeVersion = [string]$PSVersionTable.PSVersion.ToString()
$arch = [string]$env:PROCESSOR_ARCHITECTURE
try {
    $arch = [string][System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture
} catch {}

$state = [ordered]@{
    schema = 'hostops.v4.windows_install_plan.v1'
    stage = 'READ_ONLY_PLAN_NO_CHANGES'
    windows = ($env:OS -eq 'Windows_NT')
    powershell_edition = $runtimeEdition
    powershell_major = $runtimeMajor
    powershell_version = $runtimeVersion
    process_architecture = $arch
    pwsh_7_preferred_runtime = ($runtimeMajor -ge 7 -and $runtimeEdition -ceq 'Core')
    caller_non_elevated = $false
    config_present = $false
    config_schema_valid = $false
    config_disabled = $false
    source_public_control_private_declared = $false
    account_mode = 'ISOLATED_ACCOUNTS'
    single_user_mode_configured = $false
    single_user_sid_observed = $false
    native_single_user_identity_inspected = $false
    single_user_current_sid_match = $false
    single_user_runtime_enrolled = $false
    distinct_non_admin_sid_configured = $false
    untrusted_identities_isolated = $false
    pinned_binary_hashes_configured = $false
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
    # These fields describe bounded ROOT-ONLY observations, never installed
    # broker admission or independent two-account IPC identity proof.
    native_readonly_requested = [bool]$NativeReadOnly
    native_local_sid_query_complete = $false
    native_broker_local_account_found = $false
    native_transport_local_account_found = $false
    native_acl_root_query_complete = $false
    native_acl_root_policy_satisfied = $false
    native_acl_roots_inspected = 0
    native_acl_verified = $false
    native_two_account_ipc_verified = $false
    human_release_signature_verified = $false
    private_control_github_verified = $false
    ready_to_enable = $false
    block_reasons = @()
}
$reasons = [System.Collections.Generic.List[string]]::new()
$identity = $null
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
if (-not $state.caller_non_elevated) {
    $reasons.Add('CALLER_ELEVATED_OR_UNVERIFIED')
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
        # Explicit personal schema is separate from legacy two-account V4.
        # Never silently interpret an equal-SID legacy config as personal.
        $personal = ($config.schema -ceq 'hostops.config.v4.personal')
        if ($personal) { $required += 'account_mode' }
        $actual = @($config.PSObject.Properties.Name)
        $missing = @($required | Where-Object { $actual -cnotcontains $_ })
        $extras = @($actual | Where-Object { $required -cnotcontains $_ })
        $shaPattern = '^[0-9a-f]{64}$'
        $sshSha = [string]$config.ssh_keygen_sha256
        $pwshSha = [string]$config.pwsh_sha256
        $untrusted = @($config.untrusted_identities)
        $trusted = @($config.trusted_owners)
        if ($missing.Count -ne 0 -or $extras.Count -ne 0 -or
            ($config.schema -cne 'hostops.config.v4' -and -not $personal) -or
            ($personal -and ($config.account_mode -cne 'PERSONAL_SINGLE_USER' -or
                             $config.enabled -isnot [bool] -or $config.enabled -ne $false)) -or
            $sshSha -notmatch $shaPattern -or $pwshSha -notmatch $shaPattern -or
            $untrusted.Count -lt 1 -or $untrusted.Count -gt 32 -or
            $trusted.Count -lt 1 -or $trusted.Count -gt 32) { throw 'CONFIG_SCHEMA_INVALID' }
        if ($personal) {
            $prefix = '\\.\pipe\AICommerceHostOpsV4-Personal-'
            $endpoint = [string]$config.endpoint
            if (-not $endpoint.StartsWith($prefix, [StringComparison]::Ordinal) -or
                $endpoint.Substring($prefix.Length) -cnotmatch '^[A-Za-z0-9_-]{1,64}$') {
                throw 'PERSONAL_ENDPOINT_UNSAFE'
            }
        }
        $state.config_schema_valid = $true
        $state.account_mode = if ($personal) { 'PERSONAL_SINGLE_USER' } else { 'ISOLATED_ACCOUNTS' }
        $state.config_disabled = ($config.enabled -is [bool] -and $config.enabled -eq $false)
        $state.source_public_control_private_declared = (
            $config.source_repository -ceq 'trung-via/ai-commerce' -and
            $config.control_repository -ceq 'trung-via/ai-commerce-hostops-control')
        $sidPattern = '^S-1-[0-9]+(-[0-9]+){2,}$'
        $a = [string]$config.broker_identity
        $b = [string]$config.transport_identity
        $forbiddenSids = @('S-1-5-18','S-1-5-19','S-1-5-20','S-1-5-32-544')
        $state.single_user_mode_configured = (
            $personal -and $a -ceq $b -and $state.config_disabled -and
            $trusted -cnotcontains $a -and
            $untrusted -cnotcontains $a -and
            $forbiddenSids -cnotcontains $a)
        $state.single_user_sid_observed = (
            $state.windows -and $state.caller_non_elevated -and $null -ne $identity -and
            $null -ne $identity.User)
        $state.single_user_current_sid_match = (
            $state.single_user_mode_configured -and
            $state.single_user_sid_observed -and
            $a -match $sidPattern -and
            $a -ceq [string]$identity.User.Value)
        $state.distinct_non_admin_sid_configured = (
            $a -match $sidPattern -and $b -match $sidPattern -and
            $a -cne $b -and
            @('S-1-5-18','S-1-5-19','S-1-5-20','S-1-5-32-544') -cnotcontains $a -and
            @('S-1-5-18','S-1-5-19','S-1-5-20','S-1-5-32-544') -cnotcontains $b -and
            $trusted -cnotcontains $a -and $trusted -cnotcontains $b)
        $placeholderUntrusted = @($untrusted | Where-Object { ([string]$_) -notmatch $sidPattern })
        $overlap = @($untrusted | Where-Object { $trusted -ccontains ([string]$_) -or ([string]$_) -ceq $a -or ([string]$_) -ceq $b })
        $state.untrusted_identities_isolated = ($placeholderUntrusted.Count -eq 0 -and $overlap.Count -eq 0)
        $zeroSha = ('0' * 64)
        $state.pinned_binary_hashes_configured = ($sshSha -cne $zeroSha -and $pwshSha -cne $zeroSha)
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
# Human opt-in: REAL Windows read-only inventory, not a broker/transport launch.
# Bounded two exact local SID lookups and four explicit root-only ACL reads.
# No names, SIDs, paths, ACE lists, exception text or user data leave this process.
if ($NativeReadOnly) {
    if ($state.single_user_mode_configured) {
        # The Windows process token is the only identity for this mode.
        # No extra local accounts, native two-SID claims, or ACL-derived isolation.
        # The shared-token public runner can reach same-user secrets: not enrolled.
        if (-not $state.single_user_current_sid_match) {
            $reasons.Add('SINGLE_USER_CURRENT_SID_UNPROVEN')
        }
        $state.native_single_user_identity_inspected = $state.single_user_sid_observed
        $reasons.Add('PERSONAL_RUNTIME_NOT_ENROLLED')
    } elseif (-not ($state.windows -and $state.caller_non_elevated -and
              $state.config_schema_valid -and $state.distinct_non_admin_sid_configured -and
              $state.untrusted_identities_isolated)) {
        $reasons.Add('NATIVE_READONLY_PREREQUISITES_MISSING')
    } else {
        try {
            # Both values have already passed the closed SID-only regex above.
            $brokerAccounts = @(Get-CimInstance -ClassName Win32_UserAccount -Filter (
                "LocalAccount = TRUE AND SID = '" + $a + "'") -OperationTimeoutSec 3 -ErrorAction Stop)
            $transportAccounts = @(Get-CimInstance -ClassName Win32_UserAccount -Filter (
                "LocalAccount = TRUE AND SID = '" + $b + "'") -OperationTimeoutSec 3 -ErrorAction Stop)
            $state.native_local_sid_query_complete = $true
            $state.native_broker_local_account_found = (
                $brokerAccounts.Count -eq 1 -and $brokerAccounts[0].LocalAccount -eq $true -and
                $brokerAccounts[0].Disabled -eq $false)
            $state.native_transport_local_account_found = (
                $transportAccounts.Count -eq 1 -and $transportAccounts[0].LocalAccount -eq $true -and
                $transportAccounts[0].Disabled -eq $false)
        } catch {
            $reasons.Add('NATIVE_LOCAL_SID_QUERY_UNAVAILABLE')
        }

        if ($state.native_broker_local_account_found -and
            $state.native_transport_local_account_found) {
            # Do not enumerate trees, dereference unrelated paths, or print ACLs.
            # Owners must be trusted; untrusted / unapproved writers are denied.
            $trustedSids = @($trusted | ForEach-Object { [string]$_ })
            $dangerous = [int64]([Security.AccessControl.FileSystemRights]::Write -bor
                [Security.AccessControl.FileSystemRights]::Modify -bor
                [Security.AccessControl.FileSystemRights]::FullControl -bor
                [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
                [Security.AccessControl.FileSystemRights]::TakeOwnership -bor
                [Security.AccessControl.FileSystemRights]::Delete)
            $roots = @(
                @{ Path = [string]$config.policy_dir; Writers = @($a) },
                @{ Path = [string]$config.state_dir; Writers = @($a) },
                @{ Path = [string]$config.transport_state_dir; Writers = @($b) },
                @{ Path = [string]$config.release_dir; Writers = @() }
            )
            $allSecure = $true
            try {
                foreach ($root in $roots) {
                    if (-not (HasLocalItem $root.Path 'Container')) {
                        $allSecure = $false
                        break
                    }
                    $info = Get-Item -LiteralPath $root.Path -Force -ErrorAction Stop
                    if (($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                        $allSecure = $false
                        break
                    }
                    $acl = Get-Acl -LiteralPath $root.Path -ErrorAction Stop
                    $state.native_acl_roots_inspected++
                    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
                    if (-not $acl.AreAccessRulesProtected -or
                        $trustedSids -cnotcontains $owner) {
                        $allSecure = $false
                        break
                    }
                    $allowedWriters = @($trustedSids) + @($root.Writers)
                    $rules = @($acl.GetAccessRules($true, $true,
                        [Security.Principal.SecurityIdentifier]))
                    if ($rules.Count -gt 256) {
                        $allSecure = $false
                        break
                    }
                    foreach ($rule in $rules) {
                        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
                            continue
                        }
                        $mask = [int64]$rule.FileSystemRights
                        $sid = [string]$rule.IdentityReference.Value
                        if (($mask -band $dangerous) -ne 0 -and
                            $allowedWriters -cnotcontains $sid) {
                            $allSecure = $false
                            break
                        }
                    }
                    if (-not $allSecure) { break }
                }
                $state.native_acl_root_query_complete = (
                    $state.native_acl_roots_inspected -eq $roots.Count)
                $state.native_acl_root_policy_satisfied = (
                    $allSecure -and $state.native_acl_root_query_complete)
            } catch {
                $reasons.Add('NATIVE_ACL_ROOT_QUERY_UNAVAILABLE')
            }
        }
    }
    if (-not ($state.native_local_sid_query_complete -and
              $state.native_broker_local_account_found -and
              $state.native_transport_local_account_found -and
              $state.native_acl_root_policy_satisfied)) {
        $reasons.Add('NATIVE_READONLY_PROOF_INCOMPLETE')
    }
}
# This limited inventory does NOT verify child ACLs, private credential access,
# signed release authenticity, IPC HMAC/peer identity, or private GitHub control.
# Keep all activation gates hard-false until separately observed native evidence.
if (-not $state.config_disabled) { $reasons.Add('CONFIG_NOT_PROVEN_DISABLED') }
if (-not $state.source_public_control_private_declared) { $reasons.Add('REPOSITORY_BINDING_UNPROVEN') }
if ($state.account_mode -ceq 'PERSONAL_SINGLE_USER') {
    if (-not $state.single_user_current_sid_match) {
        $reasons.Add('SINGLE_USER_CURRENT_SID_UNPROVEN')
    }
    $reasons.Add('PERSONAL_RUNTIME_NOT_ENROLLED')
} elseif (-not $state.distinct_non_admin_sid_configured) {
    $reasons.Add('DISTINCT_SID_UNPROVEN')
}
if (-not $state.untrusted_identities_isolated) { $reasons.Add('UNTRUSTED_IDENTITIES_UNPROVEN') }
if (-not $state.pinned_binary_hashes_configured) { $reasons.Add('PINNED_BINARY_HASHES_UNPROVEN') }
if (-not $state.kill_marker_present) { $reasons.Add('KILL_MARKER_NOT_PROVEN') }
if (-not $state.release_signature_present) { $reasons.Add('SIGNED_RELEASE_MISSING') }
if ($state.account_mode -ceq 'PERSONAL_SINGLE_USER') {
    $reasons.Add('SINGLE_USER_NATIVE_AUTHORIZATION_NOT_VERIFIED')
} else {
    $reasons.Add('NATIVE_ACL_AND_TWO_ACCOUNT_IPC_NOT_VERIFIED')
}
$reasons.Add('PRIVATE_CONTROL_PROVENANCE_NOT_VERIFIED')
$state.block_reasons = @($reasons)
# Intentionally no enable/install/apply mode; absence of independent proof is HOLD.
$state | ConvertTo-Json -Depth 5
