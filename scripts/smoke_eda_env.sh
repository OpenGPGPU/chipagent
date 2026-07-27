#!/usr/bin/env bash
set -euo pipefail

IMAGE="${CHIPAGENT_SANDBOX_IMAGE:-chipagent/tools:latest}"

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/smoke_eda_env.sh [--image chipagent/tools:latest]

Runs a minimal smoke test inside the ChipAgent Docker toolchain image.
The image must already exist. Build it with:

  bash scripts/setup_eda_env.sh --docker

Environment:
  CHIPAGENT_SANDBOX_IMAGE  Docker image name, default chipagent/tools:latest
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      IMAGE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$IMAGE" ]]; then
  echo "empty image name" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker CLI not found" >&2
  exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Docker image '$IMAGE' not found." >&2
  echo "Build it with: bash scripts/setup_eda_env.sh --docker" >&2
  exit 1
fi

docker run --rm --network none "$IMAGE" sh -eu -c '
  cat > /tmp/adder.v <<'"'"'VERILOG'"'"'
module adder(input [7:0] a, input [7:0] b, output [8:0] sum);
  assign sum = a + b;
endmodule
VERILOG

  cat > /tmp/adder_tb.v <<'"'"'VERILOG'"'"'
module adder_tb;
  reg [7:0] a;
  reg [7:0] b;
  wire [8:0] sum;
  adder dut(.a(a), .b(b), .sum(sum));
  initial begin
    a = 8'"'"'d2;
    b = 8'"'"'d3;
    #1;
    if (sum !== 9'"'"'d5) begin
      $display("FAIL");
      $finish(1);
    end
    $display("PASS");
    $finish(0);
  end
endmodule
VERILOG

  verilator --version
  iverilog -g2012 -o /tmp/adder_tb /tmp/adder.v /tmp/adder_tb.v
  vvp /tmp/adder_tb
  yosys -p "read_verilog /tmp/adder.v; synth -top adder; stat" >/tmp/yosys.log
  magic --version >/tmp/magic.version
  if ! netgen-lvs -batch version >/tmp/netgen.version 2>&1; then
    cat /tmp/netgen.version >&2
    echo "netgen-lvs is unavailable" >&2
    exit 1
  fi
  if grep -q "NETGEN-6.2" /tmp/netgen.version || grep -qi "tetrahedral" /tmp/netgen.version; then
    echo "wrong netgen package installed; expected netgen-lvs" >&2
    exit 1
  fi
'

echo "ChipAgent EDA Docker smoke test passed for image: $IMAGE"
