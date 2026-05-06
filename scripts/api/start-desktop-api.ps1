$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$apiRoot = Join-Path $repoRoot "apps\api"
$runtimeRoot = Join-Path $repoRoot ".tmp\api"
$secretRoot = Join-Path $env:USERPROFILE ".mozhi-agent-service\api"
$apiTokenPath = Join-Path $secretRoot "api-token.txt"
$githubTokenPath = Join-Path $secretRoot "github-token.txt"
$hostName = if ($env:MOZHI_API_HOST) { $env:MOZHI_API_HOST } else { "127.0.0.1" }
$port = if ($env:MOZHI_API_PORT) { $env:MOZHI_API_PORT } else { "8080" }

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

if (-not $env:MOZHI_API_TOKEN) {
    $env:MOZHI_API_TOKEN = Read-SecretFile $apiTokenPath
}

if (-not $env:GITHUB_TOKEN) {
    $env:GITHUB_TOKEN = Read-SecretFile $githubTokenPath
}

if (-not $env:MOZHI_TASK_STORE_PATH) {
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    $env:MOZHI_TASK_STORE_PATH = Join-Path $runtimeRoot "tasks.jsonl"
}

if (-not $env:MOZHI_API_TOKEN) {
    throw "MOZHI_API_TOKEN is required. Set it in the environment or write it to $apiTokenPath."
}

Push-Location $apiRoot
try {
    python -m uvicorn mozhi_api.main:app --host $hostName --port $port
}
finally {
    Pop-Location
}
