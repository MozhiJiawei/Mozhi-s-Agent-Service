param(
    [ValidateSet("A", "B", "dev", "edge")]
    [string]$Profile = "dev"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$apiRoot = Join-Path $repoRoot "apps\api"
$runtimeRoot = Join-Path $repoRoot ".tmp\api"
$secretRoot = Join-Path $env:USERPROFILE ".mozhi-agent-service\api"
$apiTokenPath = Join-Path $secretRoot "api-token.txt"
$githubTokenPath = Join-Path $secretRoot "github-token.txt"

$normalizedProfile = @{
    A = "dev";
    dev = "dev";
    B = "edge";
    edge = "edge";
}[$Profile]

$profiles = @{
    dev = @{
        Label = "A -- Dev";
        Host = "127.0.0.1";
        Port = 8080;
    };
    edge = @{
        Label = "B -- Edge";
        Host = "0.0.0.0";
        Port = 18082;
    };
}

$selected = $profiles[$normalizedProfile]
$hostName = $selected.Host
$port = $selected.Port

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
    Write-Host ("Starting Mozhi API: {0} http://{1}:{2}" -f $selected.Label, $hostName, $port)
    python -m uvicorn mozhi_api.main:app --host $hostName --port $port
}
finally {
    Pop-Location
}
