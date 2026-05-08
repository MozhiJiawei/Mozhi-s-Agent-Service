param(
  [switch]$Once,
  [string]$RequestId
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$secretRoot = Join-Path $env:USERPROFILE ".mozhi-agent-service\api"
$githubTokenFile = Join-Path $secretRoot "github-token.txt"

if (-not $env:GITHUB_TOKEN -and (Test-Path $githubTokenFile)) {
  $env:GITHUB_TOKEN = (Get-Content -Raw $githubTokenFile).Trim()
}

$env:PYTHONPATH = Join-Path $repoRoot "apps\worker"

$argsList = @("-m", "mozhi_worker.cli", "run")
if ($Once) {
  $argsList += "--once"
}
if ($RequestId) {
  $argsList += "--request-id"
  $argsList += $RequestId
}

python @argsList
