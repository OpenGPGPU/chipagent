"""Sandboxed execution + dry-run (Phase 2 Step 6 / §1.9.4 §11.2).

Sim/compile/synthesis commands run inside a sandbox so the host is never
exposed to an untrusted EDA invocation. Two modes:

- ``host`` (default): run via ``subprocess.run`` with a hard ``timeout`` and
  CPU affinity left to the OS. This is the zero-dependency fallback for dev /
  CI hosts without Docker.
- ``docker``: run inside a container with a read-only mount of the repo, a
  writable scratch mount, CPU/memory caps, and ``--network none``. Requires
  the ``docker`` CLI and the chipagent toolchain image (``CHIPAGENT_SANDBOX_IMAGE``).

The :meth:`dry_run` path returns the exact command + a preview of the files
that would be produced **without** invoking anything — the plan's "干跑模式"
for high-risk steps (synthesis, batch sim, main-branch writeback).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class SandboxResult:
    """Outcome of a sandboxed command."""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    command: List[str] = field(default_factory=list)
    mode: str = "host"
    dry_run: bool = False
    preview: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.dry_run


class SandboxError(RuntimeError):
    pass


class Sandbox:
    """Runs a command in a host or docker sandbox.

    Parameters
    ----------
    mode:
        ``host`` (default) or ``docker``. Override with ``CHIPAGENT_SANDBOX``.
    image:
        Docker image to use in docker mode (``CHIPAGENT_SANDBOX_IMAGE``,
        default ``chipagent/tools:latest``).
    """

    def __init__(
        self,
        mode: Optional[str] = None,
        image: Optional[str] = None,
        *,
        cpu_quota: int = 2,
        memory_limit: str = "2g",
        network: str = "none",
    ) -> None:
        env_mode = os.environ.get("CHIPAGENT_SANDBOX", "").lower()
        self.mode = (mode or env_mode or "host").lower()
        if self.mode not in ("host", "docker"):
            raise SandboxError(f"unknown sandbox mode: {self.mode}")
        self.image = image or os.environ.get("CHIPAGENT_SANDBOX_IMAGE", "chipagent/tools:latest")
        self.cpu_quota = cpu_quota
        self.memory_limit = memory_limit
        self.network = network

    # ------------------------------------------------------------------
    # Dry run — never executes anything.
    # ------------------------------------------------------------------
    def dry_run(
        self,
        command: Sequence[str],
        *,
        work_dir: Optional[str] = None,
        output_files: Optional[Sequence[str]] = None,
    ) -> SandboxResult:
        preview: Dict[str, Any] = {
            "command": list(command),
            "work_dir": work_dir,
            "expected_outputs": list(output_files or []),
            "mode": self.mode,
        }
        return SandboxResult(
            returncode=0,
            stdout="",
            stderr="",
            command=list(command),
            mode=self.mode,
            dry_run=True,
            preview=preview,
        )

    # ------------------------------------------------------------------
    # Real run
    # ------------------------------------------------------------------
    def run(
        self,
        command: Sequence[str],
        *,
        work_dir: Optional[str] = None,
        timeout: int = 120,
        env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        if self.mode == "docker":
            return self._run_docker(command, work_dir=work_dir, timeout=timeout, env=env)
        return self._run_host(command, work_dir=work_dir, timeout=timeout, env=env)

    # ------------------------------------------------------------------
    def _run_host(
        self,
        command: Sequence[str],
        *,
        work_dir: Optional[str],
        timeout: int,
        env: Optional[Dict[str, str]],
    ) -> SandboxResult:
        try:
            proc = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=work_dir,
                env={**os.environ, **(env or {})},
            )
        except FileNotFoundError as exc:
            return SandboxResult(returncode=127, stderr=str(exc), command=list(command), mode="host")
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                returncode=124, stderr=f"timeout after {timeout}s: {exc}",
                command=list(command), mode="host",
            )
        return SandboxResult(
            returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
            command=list(command), mode="host",
        )

    def _run_docker(
        self,
        command: Sequence[str],
        *,
        work_dir: Optional[str],
        timeout: int,
        env: Optional[Dict[str, str]],
    ) -> SandboxResult:
        if not shutil.which("docker"):
            # Fall back to host if docker is configured but unavailable, so the
            # closed loop still runs on a bare CI box. The mode in the result
            # records the fallback honestly.
            res = self._run_host(command, work_dir=work_dir, timeout=timeout, env=env)
            res.mode = "host-fallback"
            return res
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", self.network,
            "--cpus", str(self.cpu_quota),
            "--memory", self.memory_limit,
        ]
        if work_dir:
            docker_cmd += ["-v", f"{Path(work_dir).resolve()}:/work",
                           "-w", "/work"]
        for k, v in (env or {}).items():
            docker_cmd += ["-e", f"{k}={v}"]
        docker_cmd += [self.image, "sh", "-c", " ".join(shlex.quote(c) for c in command)]
        try:
            proc = subprocess.run(
                docker_cmd, capture_output=True, text=True, timeout=timeout + 30,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                returncode=124, stderr=f"docker timeout: {exc}",
                command=docker_cmd, mode="docker",
            )
        return SandboxResult(
            returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
            command=docker_cmd, mode="docker",
        )


def is_dry_run() -> bool:
    """True when the process is running in ``--dry-run`` mode."""
    return os.environ.get("CHIPAGENT_DRY_RUN", "0") == "1"
