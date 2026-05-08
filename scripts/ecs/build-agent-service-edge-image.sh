#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
BUILD_SCRIPT="${REPO_ROOT}/scripts/docker/build.sh"

IMAGE_NAME="mozhi-agent-service-edge"
IMAGE_TAG="local"
PLATFORM="linux/amd64"
BASE_IMAGE="ubuntu:24.04"
CADDY_VERSION="2.9.1"
FRP_VERSION="0.62.1"
EXTRA_ARGS=()

die() {
  printf '[%s] ERROR: %s\n' "${SCRIPT_NAME}" "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Build the Mozhi Agent Service ECS edge image on Linux.

This is the bash equivalent of:
  scripts/ecs/build-agent-service-edge-image.ps1

Usage:
  bash scripts/ecs/build-agent-service-edge-image.sh [options]

Options:
  --image-name <name>       Image name. Default: mozhi-agent-service-edge
  --image-tag <tag>         Image tag. Default: local
  --platform <platform>     Docker platform. Default: linux/amd64
  --base-image <image>      Base image. Default: ubuntu:24.04
  --caddy-version <version> Caddy version build arg. Default: 2.9.1
  --frp-version <version>   FRP version build arg. Default: 0.62.1
  --build-arg <KEY=VALUE>   Extra docker build arg. May be repeated.
  --no-cache                Pass --no-cache to docker build.
  --progress <auto|plain|tty>
                            Pass --progress to docker build.
  -h, --help                Show this help.

Example:
  bash scripts/ecs/build-agent-service-edge-image.sh \
    --image-name mozhi-agent-service-edge \
    --image-tag local \
    --platform linux/amd64
USAGE
}

require_value() {
  local option="$1"
  local value="${2:-}"
  [[ -n "${value}" ]] || die "${option} requires a value"
}

while (($#)); do
  case "$1" in
    --image-name)
      require_value "$1" "${2:-}"
      IMAGE_NAME="$2"
      shift 2
      ;;
    --image-tag)
      require_value "$1" "${2:-}"
      IMAGE_TAG="$2"
      shift 2
      ;;
    --platform)
      require_value "$1" "${2:-}"
      PLATFORM="$2"
      shift 2
      ;;
    --base-image)
      require_value "$1" "${2:-}"
      BASE_IMAGE="$2"
      shift 2
      ;;
    --caddy-version)
      require_value "$1" "${2:-}"
      CADDY_VERSION="$2"
      shift 2
      ;;
    --frp-version)
      require_value "$1" "${2:-}"
      FRP_VERSION="$2"
      shift 2
      ;;
    --build-arg)
      require_value "$1" "${2:-}"
      EXTRA_ARGS+=("--build-arg" "$2")
      shift 2
      ;;
    --no-cache)
      EXTRA_ARGS+=("--no-cache")
      shift
      ;;
    --progress)
      require_value "$1" "${2:-}"
      EXTRA_ARGS+=("--progress" "$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

[[ -x "${BUILD_SCRIPT}" ]] || die "Build helper is not executable: ${BUILD_SCRIPT}"

exec "${BUILD_SCRIPT}" \
  --target edge \
  --image "${IMAGE_NAME}" \
  --tag "${IMAGE_TAG}" \
  --platform "${PLATFORM}" \
  --base-image "${BASE_IMAGE}" \
  --caddy-version "${CADDY_VERSION}" \
  --frp-version "${FRP_VERSION}" \
  "${EXTRA_ARGS[@]}"
