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
  openroad and sta. Set CHIPAGENT_OPENROAD_BASE_IMAGE to choose that image.
USAGE
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

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
    build_args=(
      --pull=false
      --platform "${CHIPAGENT_OPENROAD_PLATFORM:-linux/amd64}"
      --build-arg "OPENROAD_BASE_IMAGE=${CHIPAGENT_OPENROAD_BASE_IMAGE:-openroad/orfs:latest}"
    )
    docker build "${build_args[@]}" \
      -t "${CHIPAGENT_OPENROAD_IMAGE:-chipagent/openroad:latest}" \
      -f Dockerfile.openroad .
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
    IMAGE="${CHIPAGENT_OPENROAD_IMAGE:-chipagent/openroad:latest}"
    if docker image inspect "$IMAGE" >/dev/null 2>&1; then
      "$0" --smoke-openroad
    else
      echo "OpenROAD/OpenSTA heavy image not configured; skipping physical-design smoke."
    fi
    ;;
  --smoke-openroad)
    IMAGE="${CHIPAGENT_OPENROAD_IMAGE:-chipagent/openroad:latest}"
    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
      echo "OpenROAD image '$IMAGE' not found." >&2
      echo "Set CHIPAGENT_OPENROAD_IMAGE to an image containing openroad and sta." >&2
      exit 1
    fi
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
