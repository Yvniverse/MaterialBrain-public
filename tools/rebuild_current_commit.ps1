param(
    [switch]$NoCache = $true,
    [int]$HealthTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

function Invoke-Git([string[]]$GitArguments) {
    $output = & git @GitArguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($GitArguments -join ' ') failed: $output" }
    return ($output | Out-String).Trim()
}

function Require-CleanWorktree {
    $dirty = Invoke-Git @('status', '--porcelain')
    if ($dirty) { throw "Worktree is not clean. Commit or discard changes before runtime qualification.`n$dirty" }
}

Require-CleanWorktree
$sha = Invoke-Git @('rev-parse', 'HEAD')
$branch = Invoke-Git @('branch', '--show-current')
$buildTime = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
$releaseTag = ''
try {
    $releaseTag = Invoke-Git @('describe', '--tags', '--exact-match', 'HEAD')
} catch {
    $releaseTag = ''
}

$env:MATERIALBRAIN_BUILD_SHA = $sha
$env:MATERIALBRAIN_RELEASE_TAG = $releaseTag
$env:MATERIALBRAIN_BUILD_TIME_UTC = $buildTime

Write-Host "Rebuilding MaterialBrain from exact checkout"
Write-Host "  branch: $branch"
Write-Host "  commit: $sha"
Write-Host "  release tag: $releaseTag"

$buildArgs = @('compose', 'build')
if ($NoCache) { $buildArgs += '--no-cache' }
$buildArgs += @('backend', 'frontend', 'nginx')
& docker @buildArgs
if ($LASTEXITCODE -ne 0) { throw 'docker compose build failed' }

& docker compose up -d --force-recreate backend frontend nginx
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }

$webPort = if ($env:WEB_PORT) { [int]$env:WEB_PORT } else { 80 }
$base = "http://127.0.0.1:$webPort"
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$backendInfo = $null
$frontendInfo = $null
while ((Get-Date) -lt $deadline) {
    try {
        $backendInfo = Invoke-RestMethod -Uri "$base/api/v1/runtime-info" -TimeoutSec 5
        $frontendInfo = Invoke-RestMethod -Uri "$base/build-info.json" -TimeoutSec 5
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $backendInfo -or -not $frontendInfo) {
    throw "Runtime identity endpoints did not become ready within $HealthTimeoutSeconds seconds"
}

if ($backendInfo.backend_build_sha -ne $sha) {
    throw "BACKEND SHA MISMATCH: runtime=$($backendInfo.backend_build_sha) checkout=$sha"
}
if ($frontendInfo.frontend_build_sha -ne $sha) {
    throw "FRONTEND SHA MISMATCH: runtime=$($frontendInfo.frontend_build_sha) checkout=$sha"
}

Write-Host ''
Write-Host 'RUNTIME PROVENANCE PASS' -ForegroundColor Green
Write-Host "  checkout: $sha"
Write-Host "  backend : $($backendInfo.backend_build_sha)"
Write-Host "  frontend: $($frontendInfo.frontend_build_sha)"
Write-Host "  URL     : $base"
Write-Host ''
Write-Host 'Browser evidence is valid only while these three SHAs remain identical.'
