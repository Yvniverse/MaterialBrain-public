[CmdletBinding()]
param([string]$BaseUrl = 'http://127.0.0.1')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

function Compose-Capture {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & docker compose --project-directory $repositoryRoot @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed: $($output -join "`n")" }
    return ($output -join "`n").Trim()
}

function Container-Id([string]$Service) {
    return Compose-Capture -Arguments @('ps', '-q', $Service)
}

function Container-InternalIp([string]$ContainerId) {
    $addresses = & docker inspect $ContainerId --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}'
    if ($LASTEXITCODE -ne 0) { throw 'docker inspect failed' }
    return ($addresses -split '\s+' | Where-Object { $_ } | Select-Object -First 1)
}

function Container-NetworkNames([string]$ContainerId) {
    $raw = & docker inspect $ContainerId 2>&1
    if ($LASTEXITCODE -ne 0) { throw "docker inspect failed: $($raw -join "`n")" }
    $container = @($raw | ConvertFrom-Json)[0]
    return @($container.NetworkSettings.Networks.PSObject.Properties.Name)
}

function Docker-Capture {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "docker failed: $($output -join "`n")" }
    return ($output -join "`n").Trim()
}

function Probe-Status([string]$Method, [string]$Url, [string]$Body = '') {
    try {
        $parameters = @{
            Uri = $Url
            Method = $Method
            UseBasicParsing = $true
            TimeoutSec = 5
        }
        if ($Body) {
            $parameters.ContentType = 'application/json'
            $parameters.Body = $Body
        }
        return [int](Invoke-WebRequest @parameters).StatusCode
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    }
}

$nginxBefore = Container-Id -Service 'nginx'
$backendBefore = Container-Id -Service 'backend'
$backendIpBefore = Container-InternalIp -ContainerId $backendBefore
$backendNetworks = Container-NetworkNames -ContainerId $backendBefore
$nginxNetworks = Container-NetworkNames -ContainerId $nginxBefore
$proxyNetwork = @($backendNetworks | Where-Object { $nginxNetworks -contains $_ })
if ($proxyNetwork.Count -ne 1) {
    throw "Expected exactly one shared backend/nginx network, found: $($proxyNetwork -join ', ')"
}
$healthBefore = Probe-Status -Method 'GET' -Url "$BaseUrl/api/v1/health"
$loginBefore = Probe-Status -Method 'POST' -Url "$BaseUrl/api/v1/auth/login" -Body '{}'
if ($healthBefore -ne 200 -or $loginBefore -in @(0, 502, 503, 504)) {
    throw "Precondition failed: health=$healthBefore login=$loginBefore"
}

$guardName = "materialbrain-backend-ip-guard-$PID"
$guardCreated = $false
try {
    # Hold the old address while the backend is recreated. This makes the regression
    # prove Docker DNS re-resolution instead of merely receiving the same IP again.
    Compose-Capture -Arguments @('stop', 'backend') | Out-Null
    Compose-Capture -Arguments @('rm', '-f', 'backend') | Out-Null
    Docker-Capture -Arguments @(
        'create', '--name', $guardName,
        '--network', $proxyNetwork[0], '--ip', $backendIpBefore,
        'nginx:1.27-alpine'
    ) | Out-Null
    $guardCreated = $true
    Docker-Capture -Arguments @('start', $guardName) | Out-Null
    Compose-Capture -Arguments @('up', '-d', '--no-deps', 'backend') | Out-Null
}
finally {
    if ($guardCreated) {
        & docker rm -f $guardName 2>&1 | Out-Null
    }
    if (-not (Container-Id -Service 'backend')) {
        Compose-Capture -Arguments @('up', '-d', '--no-deps', 'backend') | Out-Null
    }
}
$deadline = [DateTime]::UtcNow.AddSeconds(90)
$healthAfter = 0
$loginAfter = 0
do {
    Start-Sleep -Seconds 2
    $healthAfter = Probe-Status -Method 'GET' -Url "$BaseUrl/api/v1/health"
    $loginAfter = Probe-Status -Method 'POST' -Url "$BaseUrl/api/v1/auth/login" -Body '{}'
} while (
    [DateTime]::UtcNow -lt $deadline -and
    ($healthAfter -ne 200 -or $loginAfter -in @(0, 502, 503, 504))
)

$nginxAfter = Container-Id -Service 'nginx'
$backendAfter = Container-Id -Service 'backend'
$backendIpAfter = Container-InternalIp -ContainerId $backendAfter
$dns = Compose-Capture -Arguments @('exec', '-T', 'nginx', 'getent', 'hosts', 'backend')
$nginxConfig = Compose-Capture -Arguments @(
    'exec', '-T', 'nginx', 'sh', '-lc',
    "nginx -T 2>&1 | grep -E 'resolver 127.0.0.11|proxy_pass http://\`$materialbrain_backend'"
)

if ($nginxBefore -ne $nginxAfter) { throw 'nginx restarted during the backend-only regression' }
if ($backendBefore -eq $backendAfter) { throw 'backend container was not recreated' }
if ($backendIpBefore -eq $backendIpAfter) {
    throw "backend IP did not change: before=$backendIpBefore after=$backendIpAfter"
}
if ($healthAfter -ne 200 -or $loginAfter -in @(0, 502, 503, 504)) {
    throw "Dynamic proxy recovery failed: health=$healthAfter login=$loginAfter"
}
if ($dns -notmatch [regex]::Escape($backendIpAfter)) {
    throw "Docker DNS does not match the recreated backend IP: dns=$dns backend=$backendIpAfter"
}

[ordered]@{
    regression = 'backend-recreate-without-nginx-restart'
    passed = $true
    nginx_container_unchanged = $true
    backend_container_changed = $true
    backend_ip_before = $backendIpBefore
    backend_ip_after = $backendIpAfter
    backend_ip_changed = $true
    public_health_before = $healthBefore
    public_health_after = $healthAfter
    invalid_login_before = $loginBefore
    invalid_login_after = $loginAfter
    docker_dns = $dns
    dynamic_nginx_config = [bool]$nginxConfig
} | ConvertTo-Json
