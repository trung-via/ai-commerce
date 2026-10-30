# A12 #106 read-only checkout diagnostics. Does not invoke Codex/Antigravity.
param([Parameter(Mandatory=$true)][string]$SourceSha)
$ErrorActionPreference = "Stop"
if ($SourceSha -notmatch '^[0-9a-f]{40}$') { throw "INVALID_SOURCE_SHA" }
$repo = (Get-Location).Path
$tempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$target = Join-Path $tempRoot ("a12-worktree-probe-" + [guid]::NewGuid().ToString("N"))
$created = $false
try {
  Write-Host "PRECHECK_SOURCE_SHA=$SourceSha"
  git -C $repo worktree add --detach $target $SourceSha
  if ($LASTEXITCODE -ne 0) { throw "WORKTREE_ADD_FAILED" }
  $created = $true
  $head = (git -C $target rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0 -or $head -ne $SourceSha) { throw "WORKTREE_HEAD_MISMATCH" }
  Write-Host "WORKTREE_HEAD=$head"
  git -C $target config --show-origin --get core.autocrlf
  Write-Host "CONFIG_CORE_AUTOCRLF_EXIT=$LASTEXITCODE"
  git -C $target config --show-origin --get core.eol
  Write-Host "CONFIG_CORE_EOL_EXIT=$LASTEXITCODE"
  $before = @(git -C $target status --porcelain=v1 --untracked-files=all)
  if ($LASTEXITCODE -ne 0) { throw "STATUS_BEFORE_FAILED" }
  Write-Host "BEFORE_REFRESH_CHANGED_COUNT=$($before.Count)"
  foreach ($line in ($before | Select-Object -First 80)) { Write-Host "BEFORE_REFRESH_PATH=$line" }
  git -C $target update-index -q --refresh
  Write-Host "INDEX_REFRESH_EXIT=$LASTEXITCODE"
  $after = @(git -C $target status --porcelain=v1 --untracked-files=all)
  if ($LASTEXITCODE -ne 0) { throw "STATUS_AFTER_FAILED" }
  Write-Host "AFTER_REFRESH_CHANGED_COUNT=$($after.Count)"
  foreach ($line in ($after | Select-Object -First 80)) { Write-Host "AFTER_REFRESH_PATH=$line" }
  if ($after.Count -gt 0) {
    Write-Host "DIFF_NAME_STATUS_START"
    git -C $target diff --name-status --no-ext-diff
    Write-Host "DIFF_NAME_STATUS_END"
    Write-Host "WORKTREE_PREFLIGHT_DIRTY_REAL_OR_FILTER_DIFFERENCE"
    throw "WORKTREE_PREFLIGHT_DIRTY"
  }
  Write-Host "WORKTREE_PREFLIGHT_CLEAN_AFTER_REFRESH"
}
finally {
  if ($created) {
    # Delete only this disposable probe worktree; NEVER touch real EF02 attempt folders.
    git -C $repo worktree remove --force $target
    Write-Host "PROBE_WORKTREE_CLEANUP_EXIT=$LASTEXITCODE"
  }
}
