param(
    [string]$ImageName = "mozhi-agent-service-edge",
    [string]$ImageTag = "local",
    [string]$HealthUrl = "http://localhost:18080/health",
    [string]$HttpsHealthUrl = "https://localhost:18443/health",
    [int]$MockServicePort = 18082,
    [int]$FrpsPort = 7000,
    [int]$RemoteHealthPort = 18081,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$composeFile = Join-Path $repoRoot "deploy\local-verify\docker-compose.yml"
$mockServer = Join-Path $repoRoot "deploy\local-verify\mock-desktop-service\server.py"
$tmpRoot = Join-Path $repoRoot ".tmp\local-edge-test"
$frpcConfig = Join-Path $tmpRoot "frpc.toml"
$mockStdout = Join-Path $tmpRoot "mock-stdout.log"
$mockStderr = Join-Path $tmpRoot "mock-stderr.log"
$image = "${ImageName}:${ImageTag}"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install or start Docker Desktop first."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python or make sure python is on PATH."
}

docker image inspect $image | Out-Null

$env:EDGE_IMAGE = $image

function Test-HealthOk {
    param(
        [string]$Url,
        [switch]$AllowInsecureTls
    )

    if ($AllowInsecureTls) {
        $raw = curl.exe -k -fsS --max-time 3 $Url
        $response = $raw | ConvertFrom-Json
    }
    else {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec 3
    }

    if ($response.service -ne "mozhi-agent-service-mock-desktop" -or $response.status -ne "ok") {
        throw "Unexpected health response: $($response | ConvertTo-Json -Compress)"
    }

    return $response
}

function Wait-ForHealthOk {
    param(
        [string]$Url,
        [datetime]$Deadline,
        [switch]$AllowInsecureTls
    )

    $lastError = $null
    while ((Get-Date) -lt $Deadline) {
        try {
            return Test-HealthOk -Url $Url -AllowInsecureTls:$AllowInsecureTls
        }
        catch {
            $lastError = $_.Exception.Message
        }

        Start-Sleep -Seconds 2
    }

    throw "Health check did not pass before timeout. Last error: $lastError"
}

Write-Host "Starting local edge verification stack with $image"
docker compose -f $composeFile up -d

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$mockProcess = $null

try {
    New-Item -ItemType Directory -Force -Path $tmpRoot | Out-Null

@"
serverAddr = "127.0.0.1"
serverPort = $FrpsPort
auth.method = "token"
auth.token = "local-dev-token"

[[proxies]]
name = "mozhi-health"
type = "tcp"
localIP = "host.docker.internal"
localPort = $MockServicePort
remotePort = $RemoteHealthPort
"@ | Set-Content -Path $frpcConfig -Encoding ascii

    Write-Host "Starting host-side mock desktop service on 127.0.0.1:$MockServicePort"
    $mockProcess = Start-Process -FilePath "python" `
        -ArgumentList @("`"$mockServer`"", "--host", "0.0.0.0", "--port", $MockServicePort) `
        -PassThru `
        -RedirectStandardOutput $mockStdout `
        -RedirectStandardError $mockStderr `
        -WindowStyle Hidden

    Start-Sleep -Seconds 1
    if ($mockProcess.HasExited) {
        $stdout = if (Test-Path $mockStdout) { Get-Content -Raw $mockStdout } else { "" }
        $stderr = if (Test-Path $mockStderr) { Get-Content -Raw $mockStderr } else { "" }
        throw "Mock desktop service exited early. stdout: $stdout stderr: $stderr"
    }

    Write-Host "Starting frpc inside the single edge container for tunnel verification"
    docker cp $frpcConfig "mozhi-edge-local:/tmp/frpc.toml"
    docker exec -d mozhi-edge-local /bin/bash -lc "frpc -c /tmp/frpc.toml >/tmp/frpc.log 2>&1 & echo `$! >/tmp/frpc.pid; wait `$(cat /tmp/frpc.pid)"

    $response = Wait-ForHealthOk -Url $HealthUrl -Deadline $deadline
    Write-Host "Local edge health path passed: $HealthUrl"
    $response | ConvertTo-Json -Depth 4

    $response = Wait-ForHealthOk -Url $HttpsHealthUrl -Deadline $deadline -AllowInsecureTls
    Write-Host "Local edge HTTPS health path passed: $HttpsHealthUrl"
    $response | ConvertTo-Json -Depth 4

    Write-Host "Stopping in-container frpc to verify tunnel failure is visible"
    docker exec mozhi-edge-local /bin/bash -lc "if [ -f /tmp/frpc.pid ]; then kill `$(cat /tmp/frpc.pid) 2>/dev/null || true; rm -f /tmp/frpc.pid; fi"

    $failureObserved = $false
    $failureDeadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $failureDeadline) {
        try {
            Test-HealthOk -Url $HealthUrl | Out-Null
        }
        catch {
            $failureObserved = $true
            Write-Host "Tunnel failure observed after stopping frpc: $($_.Exception.Message)"
            break
        }

        Start-Sleep -Seconds 2
    }

    if (-not $failureObserved) {
        throw "Health endpoint still succeeded after frpc was stopped."
    }

    Write-Host "Restarting in-container frpc to verify tunnel recovery"
    docker exec -d mozhi-edge-local /bin/bash -lc "frpc -c /tmp/frpc.toml >/tmp/frpc.log 2>&1 & echo `$! >/tmp/frpc.pid; wait `$(cat /tmp/frpc.pid)"
    $response = Wait-ForHealthOk -Url $HealthUrl -Deadline (Get-Date).AddSeconds($TimeoutSeconds)

    Write-Host "Local edge verification passed after tunnel recovery."
    $response | ConvertTo-Json -Depth 4
    exit 0
}
catch {
    Write-Host "Local edge verification failed. Recent logs follow." -ForegroundColor Red
    docker compose -f $composeFile ps
    docker compose -f $composeFile logs --tail=80 edge
    docker exec mozhi-edge-local /bin/bash -lc "cat /tmp/frpc.log 2>/dev/null || true" 2>$null
    if (Test-Path $mockStdout) {
        Write-Host "Mock service stdout:"
        Get-Content -Raw $mockStdout
    }
    if (Test-Path $mockStderr) {
        Write-Host "Mock service stderr:"
        Get-Content -Raw $mockStderr
    }
    throw
}
finally {
    docker exec mozhi-edge-local /bin/bash -lc "if [ -f /tmp/frpc.pid ]; then kill `$(cat /tmp/frpc.pid) 2>/dev/null || true; rm -f /tmp/frpc.pid; fi" 2>$null | Out-Null

    if ($mockProcess -and -not $mockProcess.HasExited) {
        Stop-Process -Id $mockProcess.Id -Force
    }
}
