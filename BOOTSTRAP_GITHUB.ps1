# AI Commerce — independent GitHub bootstrap (PowerShell 5.1 compatible, revision 2).
# Creates PRIVATE repository trung-via/ai-commerce from this extracted folder.
# Safe to rerun after a pre-creation failure; existing GitHub repositories are not overwritten.
$ErrorActionPreference = 'Stop'
$Repo = 'trung-via/ai-commerce'
Set-Location -LiteralPath $PSScriptRoot

# Windows PowerShell 5.1 converts stderr from native executables into PowerShell
# error records. Capture output and EXIT CODE without terminating on expected
# gh messages (e.g. "repository not found" before the initial creation).
function Invoke-NativeCommand {
  param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [string[]]$Arguments = @()
  )
  $oldPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = & $Executable @Arguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldPreference
  }
  $outputText = (@($output) | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
  return [pscustomobject]@{ ExitCode = [int]$exitCode; Text = $outputText.Trim() }
}
function Assert-Success {
  param([object]$Result,[string]$Step)
  if ($Result.ExitCode -ne 0) {
    throw "$Step failed (exit $($Result.ExitCode)):`n$($Result.Text)"
  }
}

foreach ($tool in @('git','gh')) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    throw "Missing $tool. Install Git for Windows and GitHub CLI. For gh: winget install --id GitHub.cli -e"
  }
}

$auth = Invoke-NativeCommand 'gh' @('auth','status')
if ($auth.ExitCode -ne 0) {
  throw "GitHub CLI not authenticated. Run: gh auth login --web`n$($auth.Text)"
}
$user = Invoke-NativeCommand 'gh' @('api','user','--jq','.login')
Assert-Success $user 'Verify GitHub identity'
if ($user.Text -ne 'trung-via') {
  throw "Connected GitHub account '$($user.Text)' is not trung-via. No changes made."
}
Write-Host "GitHub account VERIFIED: $($user.Text)"

$probe = Invoke-NativeCommand 'gh' @('repo','view',$Repo,'--json','name')
if ($probe.ExitCode -eq 0) {
  throw "Repository $Repo already exists. No overwrite attempted. Check https://github.com/$Repo before retrying."
}
if ($probe.Text -notmatch '(Could not resolve to a Repository|HTTP 404|Not Found|not found)') {
  throw "Cannot establish repository absence (network/permission issue). Creation not attempted:`n$($probe.Text)"
}
Write-Host "Remote repository does not exist yet: $Repo (expected)."

if (-not (Test-Path -LiteralPath '.git' -PathType Container)) {
  $init = Invoke-NativeCommand 'git' @('init','-b','main')
  Assert-Success $init 'git init'
  Write-Host $init.Text
}
$branch = Invoke-NativeCommand 'git' @('branch','--show-current')
Assert-Success $branch 'Read local branch'
if ($branch.Text -ne 'main') {
  throw "Expected local main branch, found '$($branch.Text)'. No remote action taken."
}
$name = Invoke-NativeCommand 'git' @('config','user.name')
$email = Invoke-NativeCommand 'git' @('config','user.email')
if ([string]::IsNullOrWhiteSpace($name.Text) -or [string]::IsNullOrWhiteSpace($email.Text)) {
  throw 'Git author identity missing. Set: git config --global user.name "Your Name" and git config --global user.email "you@example.com" then rerun.'
}
$add = Invoke-NativeCommand 'git' @('add','-A')
Assert-Success $add 'git add'
$diff = Invoke-NativeCommand 'git' @('diff','--cached','--quiet')
if ($diff.ExitCode -eq 1) {
  $commit = Invoke-NativeCommand 'git' @('commit','-m','chore: bootstrap independent AI Commerce governance v0.3 and blueprint v1.2')
  Assert-Success $commit 'git commit'
  Write-Host $commit.Text
} elseif ($diff.ExitCode -ne 0) {
  throw "git diff --cached failed:`n$($diff.Text)"
}
$head = Invoke-NativeCommand 'git' @('rev-parse','HEAD')
Assert-Success $head 'Read local HEAD'

Write-Host "Creating private repository $Repo and pushing main..."
$create = Invoke-NativeCommand 'gh' @('repo','create',$Repo,'--private','--source','.','--remote','origin','--push','--description','Independent AI Commerce intelligence and media growth project')
Assert-Success $create 'gh repo create/push'
Write-Host $create.Text

$verify = Invoke-NativeCommand 'gh' @('repo','view',$Repo,'--json','nameWithOwner,visibility,defaultBranchRef,url')
Assert-Success $verify 'Final remote repository verification'
Write-Host $verify.Text
$remote = Invoke-NativeCommand 'git' @('ls-remote','origin','refs/heads/main')
Assert-Success $remote 'Verify pushed remote main'
$remoteSha = (($remote.Text -split '\s+')[0]).Trim()
if ($remoteSha -ne $head.Text) {
  throw "Remote repository exists, but local HEAD ($($head.Text)) differs from remote main ($remoteSha). Inspect before pushing again."
}
Write-Host "SUCCESS: https://github.com/$Repo"
Write-Host "VERIFIED_MAIN_SHA=$remoteSha"
