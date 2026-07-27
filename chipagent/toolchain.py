"""EDA toolchain discovery and setup guidance."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sandbox import Sandbox, SandboxResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    command: str
    purpose: str
    apt_package: Optional[str] = None
    install_url: Optional[str] = None
    version_args: tuple[str, ...] = ("--version",)
    in_docker_image: bool = True
    docker_image_env: str = "CHIPAGENT_SANDBOX_IMAGE"
    default_docker_image: str = "chipagent/tools:latest"

    @property
    def docker_image(self) -> str:
        return os.environ.get(self.docker_image_env, self.default_docker_image)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("verilator", "verilator", "lint and Verilator simulation", "verilator", "https://verilator.org/guide/latest/install.html"),
    ToolSpec("iverilog", "iverilog", "Icarus Verilog simulation", "iverilog", "https://steveicarus.github.io/iverilog/", ("-V",)),
    ToolSpec("vvp", "vvp", "Icarus Verilog runtime", "iverilog", "https://steveicarus.github.io/iverilog/", ("-V",)),
    ToolSpec("yosys", "yosys", "synthesis and structural checks", "yosys", "https://yosyshq.net/yosys/"),
    ToolSpec(
        "sta",
        "sta",
        "OpenSTA timing analysis",
        None,
        "https://github.com/The-OpenROAD-Project/OpenSTA",
        in_docker_image=True,
        docker_image_env="CHIPAGENT_OPENROAD_IMAGE",
        default_docker_image="chipagent/openroad:latest",
    ),
    ToolSpec(
        "openroad",
        "openroad",
        "physical design flow",
        None,
        "https://openroad.readthedocs.io/en/latest/user/BuildLocally.html",
        in_docker_image=True,
        docker_image_env="CHIPAGENT_OPENROAD_IMAGE",
        default_docker_image="chipagent/openroad:latest",
    ),
    ToolSpec("magic", "magic", "DRC checks", "magic", "http://opencircuitdesign.com/magic/"),
    ToolSpec("netgen", "netgen-lvs", "LVS checks", "netgen-lvs", "http://opencircuitdesign.com/netgen/"),
    ToolSpec("gcc", "gcc", "C driver/HAL compilation", "gcc", "https://gcc.gnu.org/"),
)


EXTRA_BIN_DIRS: tuple[Path, ...] = (
    Path.home() / "miniconda3" / "envs" / "chia_env" / "bin",
    Path.home() / "miniconda3" / "bin",
)


def which_tool(command: str) -> Optional[str]:
    """Find an EDA command on PATH or in known local tool environments."""
    path = shutil.which(command)
    if path:
        return path
    for bin_dir in EXTRA_BIN_DIRS:
        candidate = bin_dir / command
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def docker_image_status(image: str = "chipagent/tools:latest") -> Dict[str, Any]:
    docker_path = which_tool("docker")
    if not docker_path:
        return {
            "cli_available": False,
            "image": image,
            "image_available": False,
            "error": "docker CLI not found",
        }

    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return {
            "cli_available": True,
            "path": docker_path,
            "image": image,
            "image_available": False,
            "error": str(exc),
        }

    return {
        "cli_available": True,
        "path": docker_path,
        "image": image,
        "image_available": proc.returncode == 0,
        "error": (proc.stderr or proc.stdout or "").strip() if proc.returncode != 0 else None,
    }


def _docker_netgen_lvs_available(image: str = "chipagent/tools:latest") -> bool:
    """Detect the circuit LVS Netgen, avoiding Ubuntu's unrelated mesh tool."""
    if not docker_image_status(image).get("image_available"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", image, "sh", "-c", "netgen-lvs -batch version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return False
    output = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0:
        return False
    return "NETGEN-6.2" not in output and "tetrahedral" not in output.lower()


def _docker_command_available(command: str, image: str) -> bool:
    """True when a command is present in a Docker image."""
    if not docker_image_status(image).get("image_available"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", image, "sh", "-c", f"command -v {command}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return False
    return proc.returncode == 0


def docker_image_for_command(command: str) -> str:
    """Return the configured Docker image intended to provide a command."""
    for spec in TOOL_SPECS:
        if spec.command == command:
            return spec.docker_image
    return os.environ.get("CHIPAGENT_SANDBOX_IMAGE", "chipagent/tools:latest")


def _version(command: str, args: tuple[str, ...]) -> Optional[str]:
    resolved = which_tool(command)
    if not resolved:
        return None
    try:
        proc = subprocess.run(
            [resolved, *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    text = (proc.stdout or proc.stderr or "").strip()
    return text.splitlines()[0] if text else None


def detect_tool(spec: ToolSpec) -> Dict[str, Any]:
    path = which_tool(spec.command)
    return {
        "name": spec.name,
        "command": spec.command,
        "available": path is not None,
        "path": path,
        "version": _version(spec.command, spec.version_args) if path else None,
        "purpose": spec.purpose,
        "apt_package": spec.apt_package,
        "install_url": spec.install_url,
    }


def run_eda_command(
    command: List[str],
    *,
    work_dir: Optional[str] = None,
    timeout: int = 120,
    env: Optional[Dict[str, str]] = None,
    image: Optional[str] = None,
) -> SandboxResult:
    """Run an EDA command on the host, or in the Docker tool image when needed.

    Host tools win when present. Docker fallback is enabled by default and can
    be disabled with ``CHIPAGENT_USE_DOCKER_TOOLS=0``.
    """
    if not command:
        return SandboxResult(returncode=127, stderr="empty command", mode="unavailable")

    host_tool = which_tool(command[0])
    if host_tool:
        return Sandbox(mode="host").run([host_tool, *command[1:]], work_dir=work_dir, timeout=timeout, env=env)

    docker_enabled = os.environ.get("CHIPAGENT_USE_DOCKER_TOOLS", "1").lower() not in {"0", "false", "no"}
    selected_image = image or docker_image_for_command(command[0])
    docker = docker_image_status(selected_image)
    if docker_enabled and docker.get("cli_available") and docker.get("image_available"):
        if work_dir:
            try:
                Path(work_dir).chmod(0o777)
            except OSError:
                pass
        return Sandbox(mode="docker", image=selected_image).run(command, work_dir=work_dir, timeout=timeout, env=env)

    return SandboxResult(
        returncode=127,
        stderr=f"{command[0]} not found on host and Docker tool image '{selected_image}' is unavailable",
        command=command,
        mode="unavailable",
    )


def toolchain_status() -> Dict[str, Any]:
    tools = [detect_tool(spec) for spec in TOOL_SPECS]
    docker = docker_image_status()
    docker_images = {
        "base": docker,
        "openroad": docker_image_status(os.environ.get("CHIPAGENT_OPENROAD_IMAGE", "chipagent/openroad:latest")),
    }
    spec_by_name = {spec.name: spec for spec in TOOL_SPECS}
    for tool in tools:
        spec = spec_by_name[tool["name"]]
        tool["docker_image"] = spec.docker_image
        tool["available_via_docker"] = bool(
            docker_images["base"].get("cli_available")
            and docker_image_status(spec.docker_image).get("image_available")
            and spec.in_docker_image
            and _docker_command_available(spec.command, spec.docker_image)
        )
        if tool["name"] == "netgen" and tool["available_via_docker"]:
            tool["available_via_docker"] = _docker_netgen_lvs_available(docker["image"])
        tool["usable"] = bool(tool["available"] or tool["available_via_docker"])

    missing = [tool for tool in tools if not tool["usable"]]
    available = [tool for tool in tools if tool["available"]]
    usable = [tool for tool in tools if tool["usable"]]

    apt_packages = sorted({
        tool["apt_package"]
        for tool in missing
        if tool.get("apt_package")
    })

    return {
        "status": "success",
        "available_count": len(available),
        "usable_count": len(usable),
        "missing_count": len(missing),
        "tools": tools,
        "missing_tools": [tool["name"] for tool in missing],
        "docker": docker,
        "docker_images": docker_images,
        "setup": {
            "recommended": "docker",
            "docker": {
                "build": "bash scripts/setup_eda_env.sh --docker",
                "build_base_only": "bash scripts/setup_eda_env.sh --docker-base",
                "smoke": "bash scripts/setup_eda_env.sh --smoke",
                "use": "CHIPAGENT_SANDBOX=docker CHIPAGENT_SANDBOX_IMAGE=chipagent/tools:latest",
                "script": "bash scripts/setup_eda_env.sh --docker",
            },
            "openroad_docker": {
                "image_env": "CHIPAGENT_OPENROAD_IMAGE",
                "default_image": "chipagent/openroad:latest",
                "build": "bash scripts/setup_eda_env.sh --docker-openroad",
                "base_image": "CHIPAGENT_OPENROAD_BASE_IMAGE=<image-with-openroad-and-sta> bash scripts/setup_eda_env.sh --docker-openroad",
                "smoke": "bash scripts/setup_eda_env.sh --smoke-openroad",
                "use": "CHIPAGENT_OPENROAD_IMAGE=chipagent/openroad:latest",
                "note": "OpenROAD/OpenSTA default to an adapter around openroad/orfs:latest; tools select this image automatically for openroad and sta commands.",
            },
            "apt": {
                "script": "bash scripts/setup_eda_env.sh --apt",
                "packages": apt_packages,
                "command": "sudo apt-get update && sudo apt-get install -y " + " ".join(apt_packages) if apt_packages else "",
            },
            "manual": [
                {
                    "tool": tool["name"],
                    "url": tool["install_url"],
                }
                for tool in missing
                if not tool.get("apt_package") and tool.get("install_url")
            ],
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the ChipAgent EDA toolchain.")
    parser.add_argument("command", nargs="?", default="doctor", choices=["doctor"])
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args(argv)

    data = toolchain_status()
    print(json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
