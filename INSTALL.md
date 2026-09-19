# ChipAgent Installation Guide

## Prerequisites

- Python 3.10+
- Docker (recommended for EDA tools)

## Install ChipAgent

```bash
pip install -e .
```

## EDA Tool Installation

ChipAgent requires EDA tools for verification. Use Docker (recommended) or install locally.

### Option 1: Docker (Recommended)

```bash
# Build/configure default toolchain images
bash scripts/setup_eda_env.sh --docker

# Smoke test the configured images
bash scripts/setup_eda_env.sh --smoke
```

**Images used:**
- `chipagent/tools:latest` — Verilator, Icarus Verilog, Yosys
- `chipagent/openroad:latest` — OpenROAD, OpenSTA

If Docker Hub pulls fail but you have `ubuntu:cached` locally:
```bash
CHIPAGENT_BASE_IMAGE=ubuntu:latest bash scripts/setup_eda_env.sh --docker
```

If you have a custom image with OpenROAD + OpenSTA:
```bash
CHIPAGENT_OPENROAD_BASE_IMAGE=<your-image> bash scripts/setup_eda_env.sh --docker-openroad
bash scripts/setup_eda_env.sh --smoke-openroad
```

### Option 2: Local Installation

#### Verilator (Lint + Simulation)
```bash
# Ubuntu/Debian
sudo apt-get install verilator

# macOS
brew install verilator

# From source
git clone https://github.com/verilator/verilator
cd verilator && autoconf && ./configure && make && sudo make install
```

#### Icarus Verilog (Simulation)
```bash
# Ubuntu/Debian
sudo apt-get install iverilog

# macOS
brew install icarus-verilog
```

#### Yosys (Synthesis)
```bash
# Ubuntu/Debian
sudo apt-get install yosys

# macOS
brew install yosys
```

#### OpenROAD (Physical Design)
```bash
# Docker recommended
docker pull openroad/openroad

# Or build locally: https://openroad.readthedocs.io/en/latest/user/BuildLocally.html
```

#### OpenSTA (Static Timing Analysis)
```bash
git clone https://github.com/The-OpenROAD-Project/OpenSTA.git
cd OpenSTA && mkdir build && cd build
cmake .. && make && sudo make install
```

#### Magic (DRC)
```bash
# Ubuntu/Debian
sudo apt-get install magic

# From source: http://opencircuitdesign.com/magic/
```

#### Netgen (LVS)
```bash
# From source: http://opencircuitdesign.com/netgen/
```

## Verify Installation

```bash
# Check which EDA tools are available
python -m chipagent.toolchain --pretty

# List MCP tools
python -m chipagent.mcp --list
```

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `CHIPAGENT_DISABLE_LLM` | Disable internal LLM calls (use with Claude Code) | `0` |
| `CHIPAGENT_SANDBOX` | Execution sandbox: `docker` or `host` | `host` |
| `CHIPAGENT_ALLOW_HOST_FALLBACK` | Allow host fallback when Docker unavailable | `0` |
| `CHIPAGENT_USE_TEMPORAL` | Use Temporal for durable workflows | `0` |
| `CHIPAGENT_BASE_IMAGE` | Base Docker image for tools | `ubuntu:latest` |
| `CHIPAGENT_OPENROAD_IMAGE` | OpenROAD Docker image | `chipagent/openroad:latest` |

## Running MCP Server

```bash
# With Claude Code (via .mcp.json)
# Auto-discovered when opening project

# Manual stdio server
python -m chipagent.mcp

# With custom env
CHIPAGENT_DISABLE_LLM=1 python -m chipagent.mcp
```

## Troubleshooting

**Docker permission denied:**
```bash
sudo usermod -aG docker $USER
newgrp docker
```

**Tools not found in PATH:**
```bash
# Ensure tools are in PATH or use Docker
export PATH=$PATH:/path/to/eda/tools
```

**OpenROAD/OpenSTA not found:**
```bash
# Use Docker heavy image or install manually
CHIPAGENT_OPENROAD_BASE_IMAGE=<image-with-openroad> bash scripts/setup_eda_env.sh --docker-openroad
```