#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/setup_eda_env.sh --docker   Build the default ChipAgent Docker toolchain images.
  bash scripts/setup_eda_env.sh --docker-base
                                        Build only the base ChipAgent Docker toolchain image.
  bash scripts/setup_eda_env.sh --docker-openroad
                                        Build an optional OpenROAD/OpenSTA adapter image.
  bash scripts/setup_eda_env.sh --apt      Install apt-packaged EDA tools on Ubuntu/Debian.
  bash scripts/setup_eda_env.sh --doctor   Print detected toolchain status.
  bash scripts/setup_eda_env.sh --smoke    Smoke-test the Docker toolchain image.
  bash scripts/setup_eda_env.sh --smoke-openroad
                                        Smoke-test optional OpenROAD/OpenSTA image.

Notes:
  --docker is the recommended path for reproducible local use. It builds the
  base image and then tries to configure the optional OpenROAD/OpenSTA image.
  Set CHIPAGENT_BASE_IMAGE=ubuntu:latest to build from an already-cached base
  image when Docker Hub access is blocked.
  --apt installs the tools available from common Ubuntu/Debian repositories:
  verilator, iverilog, yosys, magic, netgen-lvs, gcc, g++, make, python3-pip, git.
  --docker-openroad builds an adapter around an image that already contains
  openroad and sta on amd64, or the native source image on arm64. The Docker
  daemon architecture is detected automatically; mismatched overrides fail.
USAGE
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

docker_arch() {
  local arch
  arch="$(docker info --format '{{.Architecture}}' 2>/dev/null || true)"
  case "$arch" in
    amd64|x86_64) echo amd64 ;;
    arm64|aarch64) echo arm64 ;;
    *) echo "Unsupported or unavailable Docker daemon architecture: ${arch:-unknown}" >&2; return 1 ;;
  esac
}

default_openroad_image() {
  case "$(docker_arch)" in
    arm64) echo chipagent/openroad:arm64 ;;
    amd64) echo chipagent/openroad:amd64 ;;
  esac
}

verify_image_arch() {
  local image="$1" expected actual
  expected="$(docker_arch)"
  actual="$(docker image inspect --format '{{.Architecture}}' "$image" 2>/dev/null || true)"
  case "$actual" in
    x86_64) actual=amd64 ;;
    aarch64) actual=arm64 ;;
  esac
  if [[ "$actual" != "$expected" ]]; then
    echo "Image '$image' architecture '${actual:-unknown}' does not match Docker daemon '$expected'." >&2
    return 1
  fi
}

prepare_sv2v() {
  local version=v0.0.13
  local expected=552799a1d76cd177b9b4cc63a3e77823a3d2a6eb4ec006569288abeff28e1ff8
  local archive=.eda-cache/sv2v-Linux.zip
  mkdir -p .eda-cache
  if [[ ! -f "$archive" ]] || ! echo "$expected  $archive" | shasum -a 256 -c - >/dev/null 2>&1; then
    curl --retry 5 --retry-all-errors --connect-timeout 15 -fsSL \
      "https://github.com/zachjs/sv2v/releases/download/$version/sv2v-Linux.zip" \
      -o "$archive"
  fi
  echo "$expected  $archive" | shasum -a 256 -c -
}

case "$1" in
  --docker)
    docker build --pull=false \
      --build-arg "BASE_IMAGE=${CHIPAGENT_BASE_IMAGE:-ubuntu:22.04}" \
      -t chipagent/tools:latest -f Dockerfile .
    "$0" --docker-openroad
    ;;
  --docker-base)
    docker build --pull=false \
      --build-arg "BASE_IMAGE=${CHIPAGENT_BASE_IMAGE:-ubuntu:22.04}" \
      -t chipagent/tools:latest -f Dockerfile .
    ;;
  --docker-openroad)
    arch="$(docker_arch)"
    image="${CHIPAGENT_OPENROAD_IMAGE:-$(default_openroad_image)}"
    requested_platform="${CHIPAGENT_OPENROAD_PLATFORM:-linux/$arch}"
    if [[ "$requested_platform" != "linux/$arch" ]]; then
      echo "Refusing emulated OpenROAD platform '$requested_platform'; Docker daemon is '$arch'." >&2
      exit 1
    fi
    if [[ "$arch" == arm64 ]]; then
      required=.eda-cache/OpenROAD-flow-scripts-arm64
      if [[ ! -d "$required" ]]; then
        echo "Missing native ARM64 ORFS source cache: $required" >&2
        exit 1
      fi
      docker build --pull=false --platform linux/arm64 \
        -t "$image" -f Dockerfile.openroad.arm64 .
    else
      prepare_sv2v
      docker build --pull=false --platform linux/amd64 \
        --build-arg "OPENROAD_BASE_IMAGE=${CHIPAGENT_OPENROAD_BASE_IMAGE:-openroad/orfs:latest}" \
        -t "$image" -f Dockerfile.openroad .
    fi
    verify_image_arch "$image"
    ;;
  --apt)
    if ! command -v apt-get >/dev/null 2>&1; then
      echo "apt-get not found. Use --docker or install tools manually." >&2
      exit 1
    fi
    sudo apt-get update
    sudo apt-get install -y \
      verilator \
      iverilog \
      yosys \
      magic \
      netgen-lvs \
      gcc \
      g++ \
      make \
      python3 \
      python3-pip \
      python3-venv \
      git
    ;;
  --doctor)
    python -m chipagent.toolchain --pretty
    ;;
  --smoke)
    bash scripts/smoke_eda_env.sh
    IMAGE="${CHIPAGENT_OPENROAD_IMAGE:-$(default_openroad_image)}"
    if docker image inspect "$IMAGE" >/dev/null 2>&1; then
      "$0" --smoke-openroad
    else
      echo "OpenROAD/OpenSTA heavy image not configured; skipping physical-design smoke."
    fi
    ;;
  --smoke-openroad)
    IMAGE="${CHIPAGENT_OPENROAD_IMAGE:-$(default_openroad_image)}"
    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
      echo "OpenROAD image '$IMAGE' not found." >&2
      echo "Set CHIPAGENT_OPENROAD_IMAGE to an image containing openroad and sta." >&2
      exit 1
    fi
    verify_image_arch "$IMAGE"
    docker run --rm --network none "$IMAGE" sh -eu -c '
      openroad -version
      sta -version
    '
    echo "ChipAgent OpenROAD Docker smoke test passed for image: $IMAGE"
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage
    exit 2
    ;;
esac
