param(
    [int]$Port = 18082
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$apiRoot = Join-Path $repoRoot "apps\api"
$runtimeRoot = Join-Path $repoRoot ".tmp\api"
$logDir = Join-Path $runtimeRoot "logs"
$secretRoot = Join-Path $env:USERPROFILE ".mozhi-agent-service\api"
$apiTokenPath = Join-Path $secretRoot "api-token.txt"
$githubTokenPath = Join-Path $secretRoot "github-token.txt"
$outLog = Join-Path $logDir "edge-api-$Port.out.log"
$errLog = Join-Path $logDir "edge-api-$Port.err.log"

function Read-SecretFile($path) {
    if (-not (Test-Path -LiteralPath $path)) {
        return $null
    }

    $value = (Get-Content -Raw -LiteralPath $path).Trim()
    if ($value.Length -eq 0) {
        return $null
    }

    return $value
}

New-Item -ItemType Directory -Force -Path $runtimeRoot, $logDir | Out-Null

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "Stopping existing API process PID $($process.Id) on port $Port..."
        Stop-Process -Id $process.Id -Force
    }
}

Start-Sleep -Seconds 1

$env:MOZHI_API_TOKEN = Read-SecretFile $apiTokenPath
$env:GITHUB_TOKEN = Read-SecretFile $githubTokenPath
$env:MOZHI_TASK_STORE_PATH = Join-Path $runtimeRoot "tasks.jsonl"

if (-not $env:MOZHI_API_TOKEN) {
    throw "MOZHI_API_TOKEN is required. Set it in the environment or write it to $apiTokenPath."
}

Write-Host "Starting Mozhi API Edge on 0.0.0.0:$Port..."
Start-Process `
    -FilePath python `
    -ArgumentList @("-m", "uvicorn", "mozhi_api.main:app", "--host", "0.0.0.0", "--port", "$Port") `
    -WorkingDirectory $apiRoot `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden

Start-Sleep -Seconds 4

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Write-Host "API failed to start. Recent stderr:"
    Get-Content -Path $errLog -Tail 80 -ErrorAction SilentlyContinue
    exit 1
}

$health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
Write-Host "Started. PID $($listener.OwningProcess). Health: $($health.status)."
Write-Host "URL: http://127.0.0.1:$Port/health"
Write-Host "Logs: $outLog"
