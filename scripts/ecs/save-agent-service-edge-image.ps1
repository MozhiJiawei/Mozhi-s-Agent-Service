param(
    [string]$ImageName = "mozhi-agent-service-edge",
    [string]$ImageTag = "local",
    [string]$OutputDirectory = "dist\docker"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$outputRoot = Join-Path $repoRoot $OutputDirectory
$image = "${ImageName}:${ImageTag}"
$safeTag = $ImageTag -replace '[^A-Za-z0-9_.-]', '-'
$outputFile = Join-Path $outputRoot "$ImageName-$safeTag.tar"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install or start Docker Desktop first."
}

docker image inspect $image | Out-Null

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

Write-Host "Saving $image to $outputFile"
docker save --output $outputFile $image

Write-Host "Saved image archive: $outputFile"
Write-Host "After docker load succeeds on ECS, delete the uploaded tar file to protect the 40GB system disk."

