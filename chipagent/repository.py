"""Repository layer.

Implements the "read context + write artifacts + optional git commit" flow
described in Section 3.2 of the phase 1 plan. Phase 1 keeps this deliberately
small: artifacts are written to a designated output directory (never the main
branch), and an opt-in git commit path is available when
``CHIPAGENT_USE_GIT=1`` is set.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import TaskObject, WorkflowState

# File suffixes the repository layer will load as design context.
_CONTEXT_SUFFIXES = {".md", ".txt", ".v", ".sv", ".vh", "svh", ".json", ".yaml", ".yml"}


class Repository:
    """Reads context files and writes generated artifacts to disk."""

    def __init__(self, repo_root: Optional[str] = None, use_git: bool = False) -> None:
        self._repo_root = Path(repo_root) if repo_root else None
        self._use_git = use_git

    # ------------------------------------------------------------------
    # Read context
    # ------------------------------------------------------------------
    def load_context(
        self,
        context_path: Optional[str] = None,
        context_dir: Optional[str] = None,
    ) -> str:
        """Return concatenated context text from a file and/or a directory tree."""
        parts: List[str] = []

        if context_path:
            path = Path(context_path)
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))

        if context_dir:
            directory = Path(context_dir)
            if directory.exists():
                for file_path in sorted(directory.rglob("*")):
                    if file_path.is_file() and file_path.suffix.lower() in _CONTEXT_SUFFIXES:
                        try:
                            parts.append(file_path.read_text(encoding="utf-8"))
                        except Exception:
                            continue

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Write artifacts
    # ------------------------------------------------------------------
    def persist(
        self,
        output_dir: Optional[str],
        task: Dict[str, Any],
        code: str,
        design_notes: str,
        checks: Dict[str, Any],
        logs: List[Dict[str, Any]],
        log_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """Write RTL code and a JSON workflow report. Returns artifact paths."""
        if not output_dir:
            return {}

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        module_name = task.get("module_name") or "generated_module"
        code_path = out / f"{module_name}.v"
        design_path = out / f"{module_name}.md"
        report_path = out / "workflow_report.json"

        code_path.write_text(code, encoding="utf-8")
        design_path.write_text(
            f"# 设计说明 — {module_name}\n\n{design_notes}\n",
            encoding="utf-8",
        )
        report = {
            "task": task,
            "checks": checks,
            "logs": logs,
            "design_notes": design_notes,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        artifacts: Dict[str, str] = {
            "code_path": str(code_path),
            "design_path": str(design_path),
            "report_path": str(report_path),
        }

        if log_dir:
            from .logging_utils import write_log_file

            log_path = write_log_file(log_dir, module_name, logs)
            if log_path:
                artifacts["log_path"] = log_path

        if self._use_git and self._repo_root is not None:
            self._git_commit(out, module_name, artifacts)

        return artifacts

    def persist_artifacts(
        self,
        output_dir: Optional[str],
        files: Dict[str, str],
        report_data: Dict[str, Any],
        task: Dict[str, Any],
        logs: List[Dict[str, Any]],
        log_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """Write multiple named artifacts + one JSON workflow report.

        ``files`` maps filename -> content. The report aggregates task, logs,
        and whatever summary ``report_data`` carries (checks, sim, alignment).
        """
        if not output_dir:
            return {}

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        module_name = task.get("module_name") or "generated_module"
        artifacts: Dict[str, str] = {}
        for fname, content in files.items():
            p = out / fname
            p.write_text(content, encoding="utf-8")
            artifacts[str(fname)] = str(p)

        report_path = out / "workflow_report.json"
        report = {"task": task, "logs": logs, **report_data}
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        artifacts["report_path"] = str(report_path)

        if log_dir:
            from .logging_utils import write_log_file

            log_path = write_log_file(log_dir, module_name, logs)
            if log_path:
                artifacts["log_path"] = log_path

        if self._use_git and self._repo_root is not None:
            self._git_commit(out, module_name, artifacts)

        return artifacts

    # ------------------------------------------------------------------
    # Optional git integration (off by default in phase 1)
    # ------------------------------------------------------------------
    def _git_commit(self, out: Path, module_name: str, artifacts: Dict[str, str]) -> None:
        """Stage generated artifacts and create a commit on the current branch.

        Disabled unless ``CHIPAGENT_USE_GIT=1``. Even then it only commits the
        generated output directory contents — it never modifies source trees.
        """
        try:
            subprocess.run(["git", "init"], cwd=self._repo_root, check=False, capture_output=True)
            subprocess.run(
                ["git", "add", str(out)],
                cwd=self._repo_root,
                check=False,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", f"chipagent: generate {module_name} RTL"],
                cwd=self._repo_root,
                check=False,
                capture_output=True,
            )
        except FileNotFoundError:
            # git not installed; skip silently — artifacts are still on disk.
            return


def load_context(context_path: Optional[str] = None, context_dir: Optional[str] = None) -> str:
    """Functional wrapper kept for the legacy workflow entry point."""
    return Repository().load_context(context_path=context_path, context_dir=context_dir)
