#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

DEFAULT_IMAGE="mozhi-agent-service-edge"
DEFAULT_PLATFORM="linux/amd64"
DEFAULT_BASE_IMAGE="ubuntu:24.04"
DEFAULT_CADDY_VERSION="2.9.1"
DEFAULT_FRP_VERSION="0.62.1"

TARGET="edge"
IMAGE="${DEFAULT_IMAGE}"
TAG=""
PLATFORM="${DEFAULT_PLATFORM}"
BASE_IMAGE="${DEFAULT_BASE_IMAGE}"
CONTEXT=""
DOCKERFILE=""
CADDY_VERSION="${DEFAULT_CADDY_VERSION}"
FRP_VERSION="${DEFAULT_FRP_VERSION}"
NO_CACHE="false"
PROGRESS=""
EXTRA_BUILD_ARGS=()

log() {
  printf '[%s] %s\n' "${SCRIPT_NAME}" "$*"
}

die() {
  printf '[%s] ERROR: %s\n' "${SCRIPT_NAME}" "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Build Mozhi Agent Service Docker images on Linux.

Usage:
  bash scripts/docker/build.sh [options]

Options:
  --target <edge|service|all|api|worker>
      Image target to build. The current repository ships the edge image.
      "service" is an alias for "edge"; "all" builds every available image.
      api/worker targets fail clearly until their Dockerfiles are added.
      Default: edge
  --image <name>
      Docker image repository/name. Default: mozhi-agent-service-edge
  --tag <tag>
      Docker image tag. Default: current git short SHA, or "local" outside git.
  --platform <platform>
      Docker platform passed to docker build. Default: linux/amd64
  --base-image <image>
      Base image used by the edge Dockerfile. Default: ubuntu:24.04
  --context <path>
      Build context path. Default for edge: deploy/ecs/agent-service-edge
  --dockerfile <path>
      Dockerfile path. Default for edge: deploy/ecs/agent-service-edge/Dockerfile
  --caddy-version <version>
      Caddy version build arg for the edge image. Default: 2.9.1
  --frp-version <version>
      FRP version build arg for the edge image. Default: 0.62.1
  --build-arg <KEY=VALUE>
      Extra docker build argument. May be provided multiple times.
  --no-cache
      Pass --no-cache to docker build.
  --progress <auto|plain|tty>
      Pass --progress to docker build.
  -h, --help
      Show this help.

Examples:
  bash scripts/docker/build.sh
  bash scripts/docker/build.sh --image mozhi-agent-service-edge --tag local
  bash scripts/docker/build.sh --target edge --platform linux/amd64 --tag "$(git rev-parse --short HEAD)"
USAGE
}

default_tag() {
  if git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "${REPO_ROOT}" rev-parse --short HEAD
  else
    printf 'local\n'
  fi
}

require_value() {
  local option="$1"
  local value="${2:-}"
  [[ -n "${value}" ]] || die "${option} requires a value"
}

parse_args() {
  while (($#)); do
    case "$1" in
      --target)
        require_value "$1" "${2:-}"
        TARGET="$2"
        shift 2
        ;;
      --image)
        require_value "$1" "${2:-}"
        IMAGE="$2"
        shift 2
        ;;
      --tag)
        require_value "$1" "${2:-}"
        TAG="$2"
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
      --context)
        require_value "$1" "${2:-}"
        CONTEXT="$2"
        shift 2
        ;;
      --dockerfile)
        require_value "$1" "${2:-}"
        DOCKERFILE="$2"
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
        EXTRA_BUILD_ARGS+=("--build-arg" "$2")
        shift 2
        ;;
      --no-cache)
        NO_CACHE="true"
        shift
        ;;
      --progress)
        require_value "$1" "${2:-}"
        PROGRESS="$2"
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
}

abs_path() {
  local path="$1"
  if [[ "${path}" = /* ]]; then
    printf '%s\n' "${path}"
  else
    printf '%s\n' "${REPO_ROOT}/${path}"
  fi
}

ensure_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker CLI was not found in PATH"
  docker version >/dev/null || die "Docker daemon is not reachable"
}

docker_build_supports_progress() {
  docker build --help 2>/dev/null | grep -q -- '--progress'
}

build_edge() {
  local context_path dockerfile_path image_ref
  context_path="$(abs_path "${CONTEXT:-deploy/ecs/agent-service-edge}")"
  dockerfile_path="$(abs_path "${DOCKERFILE:-deploy/ecs/agent-service-edge/Dockerfile}")"
  image_ref="${IMAGE}:${TAG}"

  [[ -d "${context_path}" ]] || die "Build context does not exist: ${context_path}"
  [[ -f "${dockerfile_path}" ]] || die "Dockerfile does not exist: ${dockerfile_path}"

  local cmd=(
    docker build
    --platform "${PLATFORM}"
    --build-arg "BASE_IMAGE=${BASE_IMAGE}"
    --build-arg "CADDY_VERSION=${CADDY_VERSION}"
    --build-arg "FRP_VERSION=${FRP_VERSION}"
    --tag "${image_ref}"
    --file "${dockerfile_path}"
  )

  if [[ "${NO_CACHE}" == "true" ]]; then
    cmd+=(--no-cache)
  fi

  if [[ -n "${PROGRESS}" ]]; then
    if docker_build_supports_progress; then
      cmd+=(--progress "${PROGRESS}")
    else
      log "Docker build does not support --progress; continuing without it."
    fi
  fi

  cmd+=("${EXTRA_BUILD_ARGS[@]}")
  cmd+=("${context_path}")

  log "Repository root: ${REPO_ROOT}"
  log "Target: edge"
  log "Image: ${image_ref}"
  log "Platform: ${PLATFORM}"
  log "Base image: ${BASE_IMAGE}"
  log "Dockerfile: ${dockerfile_path}"
  log "Context: ${context_path}"
  log "Caddy version: ${CADDY_VERSION}"
  log "FRP version: ${FRP_VERSION}"

  "${cmd[@]}"
  log "Built ${image_ref}"
}

build_unavailable_target() {
  local target="$1"
  die "Target '${target}' is not available yet because this repository does not ship an ${target} Dockerfile. Current supported target: edge."
}

main() {
  parse_args "$@"
  TAG="${TAG:-$(default_tag)}"

  case "${TARGET}" in
    edge|service)
      ensure_docker
      build_edge
      ;;
    all)
      ensure_docker
      build_edge
      ;;
    api|worker)
      build_unavailable_target "${TARGET}"
      ;;
    *)
      die "Unsupported target '${TARGET}'. Supported targets: edge, service, all. Reserved targets: api, worker."
      ;;
  esac
}

main "$@"
