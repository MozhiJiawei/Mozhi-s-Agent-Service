param(
    [string]$ImageName = "mozhi-agent-service-edge",
    [string]$ImageTag = "local",
    [string]$Platform = "linux/amd64",
    [string]$CaddyVersion = "2.9.1",
    [string]$FrpVersion = "0.62.1"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$dockerfile = Join-Path $repoRoot "deploy\ecs\agent-service-edge\Dockerfile"
$context = Join-Path $repoRoot "deploy\ecs\agent-service-edge"
$image = "${ImageName}:${ImageTag}"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install or start Docker Desktop first."
}

docker version | Out-Null

Write-Host "Building $image for $Platform"
docker build `
    --platform $Platform `
    --build-arg "CADDY_VERSION=$CaddyVersion" `
    --build-arg "FRP_VERSION=$FrpVersion" `
    --tag $image `
    --file $dockerfile `
    $context

Write-Host "Built $image"

