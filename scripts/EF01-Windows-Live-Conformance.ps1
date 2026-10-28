# EF01 Windows live conformance: one explicitly selected executor per invocation.
# Run twice with distinct AttemptId values: once for codex and once for antigravity.
# This is a local engineering check, NOT EF02 remote dispatch or native continuation.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('codex','antigravity')]
    [string]$Executor,
    [Parameter(Mandatory=$true)]
    [string]$Model,
    [Parameter(Mandatory=$true)]
    [string]$Effort,
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,60}$')]
    [string]$AttemptId,
    [string]$Repo = '.'
)
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path -LiteralPath $Repo).Path
if (-not (Test-Path -LiteralPath (Join-Path $repoPath 'src/ai_commerce/engineering/cli.py'))) {
    throw 'EF01_CODE_NOT_CHECKED_OUT: select the EF01 implementation branch first.'
}
$binary = if ($Executor -eq 'codex') { 'codex' } else { 'agy' }
# Deliberately leave binary availability to EF01 runner so missing CLI yields
# a normalized EXECUTOR_UNAVAILABLE attempt receipt with diagnostic logs.
$head = (& git -C $repoPath rev-parse HEAD)
if ($LASTEXITCODE -ne 0) { throw 'GIT_HEAD_UNAVAILABLE' }
$head = $head.Trim()
$worktree = Join-Path $env:TEMP ("ai-commerce-ef01-" + $AttemptId)
$inputs = Join-Path $repoPath ('.local/ef01-input/' + $AttemptId)
$outDir = Join-Path $repoPath ('.local/ef01-results/' + $AttemptId)
if ((Test-Path $worktree) -or (Test-Path $inputs) -or (Test-Path $outDir)) {
    throw 'ATTEMPT_ALREADY_EXISTS: choose a new AttemptId; no overwrite.'
}
$null = New-Item -ItemType Directory -Path $inputs -Force
& git -C $repoPath worktree add --detach $worktree $head
if ($LASTEXITCODE -ne 0) { throw 'GIT_WORKTREE_CREATE_FAILED' }
$testName = "test_ef01_live_$Executor.py"
$instructionFile = Join-Path $inputs 'instruction.txt'
$verifyFile = Join-Path $inputs 'verify.json'
$prompt = @"
Implement exactly one small, real pytest code change in tests/$testName in this AI Commerce workspace.
Write a normal pytest test importing ExecutorSelection from ai_commerce.engineering.profile.
Test that ExecutorSelection('$Executor', 'smoke-model', 'high') preserves the selected executor/model/effort.
Do not modify any other file, do not run git commit, and do not invoke other executors.
"@
Set-Content -LiteralPath $instructionFile -Value $prompt -Encoding UTF8
$verify = @('python', '-m', 'pytest', ('tests/' + $testName), '-q')
Set-Content -LiteralPath $verifyFile -Value (ConvertTo-Json -InputObject $verify -Compress) -Encoding UTF8
$originalPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $worktree 'src'
    & python -m ai_commerce.engineering --repo $worktree --work-id ("EF01-LIVE-" + $Executor) --attempt-id $AttemptId --executor $Executor --model $Model --effort $Effort --instruction-file $instructionFile --verify-json $verifyFile --out-dir $outDir --allow-path ("tests/" + $testName)
    $exitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $originalPythonPath
}
Write-Host "SOURCE_SHA=$head"
Write-Host "WORKTREE=$worktree"
Write-Host "EVIDENCE=$outDir"
Write-Host "EF01_EXIT=$exitCode"
exit $exitCode
