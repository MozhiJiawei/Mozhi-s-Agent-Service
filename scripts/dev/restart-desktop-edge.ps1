param(
    [string]$ApiHost = "0.0.0.0",
    [int]$ApiPort = 18082,
    [string]$PublicBaseUrl = "http://39.105.78.135",
    [string]$FrpcContainerName = "mozhi-ecs-frpc-test",
    [string]$FrpcImage = "mozhi-agent-service-edge:local",
    [string]$FrpcConfigPath = "$env:USERPROFILE\.mozhi-agent-service\edge\frpc-ecs.toml",
    [switch]$SkipPublicHealthCheck
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$apiStartScript = Join-Path $repoRoot "scripts\api\start-desktop-api.ps1"
$logRoot = Join-Path $repoRoot ".runtime\api\logs"
$apiOutLog = Join-Path $logRoot "api-$ApiPort.out.log"
$apiErrLog = Join-Path $logRoot "api-$ApiPort.err.log"

function Stop-ApiOnPort {
    param([int]$Port)

    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $pids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)

    foreach ($pidValue in $pids) {
        if (-not $pidValue) {
            continue
        }

        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }

        Write-Host "Stopping existing API listener on port $Port (PID $pidValue, $($process.ProcessName))"
        Stop-Process -Id $pidValue -Force
    }
}

function Wait-ForHttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return $response
            }

            $lastError = "HTTP $($response.StatusCode)"
        }
        catch {
            $lastError = $_.Exception.Message
        }

        Start-Sleep -Seconds 1
    }

    throw "Timed out waiting for $Url. Last error: $lastError"
}

function Ensure-FrpcContainer {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker CLI was not found. Start Docker Desktop or install Docker first."
    }

    if (-not (Test-Path -LiteralPath $FrpcConfigPath)) {
        throw "FRP client config not found: $FrpcConfigPath"
    }

    docker image inspect $FrpcImage | Out-Null

    $existingId = docker ps -aq --filter "name=^/$FrpcContainerName$"
    if ($existingId) {
        Write-Host "Restarting FRP client container $FrpcContainerName"
        docker restart $FrpcContainerName | Out-Null
        return
    }

    Write-Host "Creating FRP client container $FrpcContainerName"
    docker run -d `
        --name $FrpcContainerName `
        --restart unless-stopped `
        -v "${FrpcConfigPath}:/tmp/frpc.toml:ro" `
        --entrypoint frpc `
        $FrpcImage `
        -c /tmp/frpc.toml | Out-Null
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

Stop-ApiOnPort -Port $ApiPort

Write-Host "Starting desktop API on ${ApiHost}:$ApiPort"
$apiCommand = "`$env:MOZHI_API_HOST='$ApiHost'; `$env:MOZHI_API_PORT='$ApiPort'; & '$apiStartScript'"
$apiProcess = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $apiCommand) `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $apiOutLog `
    -RedirectStandardError $apiErrLog

Ensure-FrpcContainer

$localHealthUrl = "http://127.0.0.1:$ApiPort/health"
Write-Host "Checking local health: $localHealthUrl"
Wait-ForHttpOk -Url $localHealthUrl | Out-Null

if (-not $SkipPublicHealthCheck) {
    $publicHealthUrl = "$PublicBaseUrl/health"
    Write-Host "Checking public health: $publicHealthUrl"
    Wait-ForHttpOk -Url $publicHealthUrl | Out-Null
}

$apiListener = Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
$frpcStatus = docker ps --filter "name=^/$FrpcContainerName$" --format "{{.Names}} {{.Status}} {{.Image}}"

Write-Host ""
Write-Host "Desktop edge restart complete."
Write-Host "API PID: $($apiListener.OwningProcess)"
Write-Host "API URL: http://127.0.0.1:$ApiPort"
Write-Host "Public URL: $PublicBaseUrl"
Write-Host "FRP: $frpcStatus"
Write-Host "Logs:"
Write-Host "  stdout: $apiOutLog"
Write-Host "  stderr: $apiErrLog"
Write-Host ""
Write-Host "Worker and Codex were not started."
