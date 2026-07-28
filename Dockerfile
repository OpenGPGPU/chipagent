# ChipAgent Base EDA Toolchain Image
#
# Provides a sandboxed execution environment for common sim/compile/synthesis
# and layout verification tools. OpenROAD/OpenSTA live in an optional heavy
# image configured by CHIPAGENT_OPENROAD_IMAGE.
#
# Build:
#   docker build -t chipagent/tools:latest -f Dockerfile .
#
# Usage (automatic via sandbox.py):
#   CHIPAGENT_SANDBOX=docker chipagent "..." --output-dir ./generated
#
# Tools included:
#   - Verilator (lint + simulation)
#   - Icarus Verilog (simulation)
#   - Yosys (synthesis estimation)
#   - Magic (DRC)
#   - Netgen LVS (LVS)
#   - GCC (driver/HAL compilation)
#   - Python 3.10+ (chipagent package)
#
# OpenROAD/OpenSTA are not installed by this base image because upstream binary
# availability varies by platform. chipagent.toolchain reports their status and
# links to the supported install paths.

ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}

# Avoid interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

# Install EDA tools + build essentials
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Create a non-root user for sandboxed execution
RUN useradd -m -s /bin/bash chipagent
USER chipagent
WORKDIR /work

# Default command: shell (sandbox.py overrides with specific commands)
CMD ["sh"]
