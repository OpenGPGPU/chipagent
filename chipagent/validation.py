"""Validation layer (Section 3.5 / 10.8).

Performs basic structural lint and syntax checks on generated Verilog. If a
real tool (verilator/iverilog) is present on PATH it is used; otherwise a
built-in structural checker covers the common failure modes
(unbalanced module/endmodule, unbalanced parentheses, missing semicolons).
This implements the plan's "lightweight validation path first, do not depend
on complex EDA tools" risk mitigation (Section 8.2).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Dict, List, Tuple


class VerilogLinter:
    """Lint + syntax checker for generated RTL."""

    def __init__(self, tool: str = "auto") -> None:
        self._tool = tool

    def run(self, code: str, code_path: str | None = None) -> Dict[str, Any]:
        """Return ``{"lint": ..., "syntax": ..., "issues": [...]}``."""
        issues: List[str] = []
        syntax = self._structural_check(code, issues)
        lint = "passed" if syntax == "passed" and not issues else "failed"

        # Try a real tool if one is available; structural result is the floor.
        tool_issues: List[str] = []
        tool_syntax = self._run_external_tool(code, code_path, tool_issues)
        if tool_syntax is not None:
            syntax = tool_syntax
            issues.extend(tool_issues)
            lint = "passed" if syntax == "passed" and not issues else "failed"

        return {"lint": lint, "syntax": syntax, "issues": issues}

    # ------------------------------------------------------------------
    # Built-in structural checks
    # ------------------------------------------------------------------
    def _structural_check(self, code: str, issues: List[str]) -> str:
        module_count = len(re.findall(r"\bmodule\s+\w+", code))
        endmodule_count = len(re.findall(r"\bendmodule\b", code))
        if module_count == 0:
            issues.append("no 'module' declaration found")
        if module_count != endmodule_count:
            issues.append(f"unbalanced module/endmodule: {module_count} module vs {endmodule_count} endmodule")

        # Strip comments before paren/semicolon accounting.
        stripped = re.sub(r"//[^\n]*", "", code)
        stripped = re.sub(r"/\*[\s\S]*?\*/", "", stripped)

        if stripped.count("(") != stripped.count(")"):
            issues.append(f"unbalanced parentheses: {stripped.count('(')} '(' vs {stripped.count(')')} ')'")

        # Conservative missing-semicolon check: only flag procedural
        # assignment statements (`<=` or `assign`) that fail to terminate.
        # Declarations (parameter/localparam/port lists) legally end with a
        # comma or no terminator, so they are deliberately not flagged here.
        decl_prefixes = (
            "parameter", "localparam", "input", "output", "inout",
            "module", "endmodule", "begin", "end", "else", "if", "for",
            "while", "always", "case", "endcase", "default", "fork", "join",
            "generate", "endgenerate", "function", "endfunction",
            "task", "endtask", "assign", "wire", "reg", "logic",
        )
        for line in stripped.splitlines():
            stripped_line = line.rstrip()
            if not stripped_line:
                continue
            bare = stripped_line.lstrip()
            if bare.startswith(decl_prefixes):
                continue
            if "<=" not in stripped_line:
                continue  # only procedural assignments
            if stripped_line[-1] in ";,(){}:":
                continue
            issues.append(f"possible missing ';' on: {stripped_line.strip()[:80]}")
            break  # one such warning is enough

        # Port-declaration lines whose separator comma landed inside a //
        # comment (a common LLM/template bug that breaks the port list).
        # Run against the ORIGINAL code (with comments), not `stripped`.
        # The last port (followed by a closing ")") legitimately has no comma.
        raw_lines = code.splitlines()
        for idx, line in enumerate(raw_lines):
            m = re.match(r"^\s*(input|output|inout)\b.*?//", line)
            if not m:
                continue
            before_comment = line.split("//", 1)[0].rstrip()
            if before_comment.endswith(",") or before_comment.endswith(")"):
                continue
            # Look ahead to the next non-empty line: if it closes the port
            # list, this is the last port and a missing comma is fine.
            following = next(
                (raw_lines[j].strip() for j in range(idx + 1, len(raw_lines)) if raw_lines[j].strip()),
                "",
            )
            if following.startswith(")"):
                continue
            issues.append(
                f"port declaration missing trailing comma before comment: {line.strip()[:80]}"
            )
            break

        return "passed" if not issues else "failed"

    # ------------------------------------------------------------------
    # External tool (verilator/iverilog) when available
    # ------------------------------------------------------------------
    def _run_external_tool(self, code: str, code_path: str | None, issues: List[str]) -> str | None:
        if not code_path:
            return None
        tool = self._resolve_tool()
        if tool is None:
            return None
        try:
            if tool == "verilator":
                proc = subprocess.run(
                    ["verilator", "--lint-only", "--top-module", "_", code_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:  # iverilog
                proc = subprocess.run(
                    ["iverilog", "-t", "null", code_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            if proc.returncode == 0:
                return "passed"
            issues.append(f"{tool}: {proc.stderr.strip()[:500]}")
            return "failed"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def _resolve_tool(self) -> str | None:
        if self._tool != "auto":
            return self._tool if shutil.which(self._tool) else None
        for candidate in ("verilator", "iverilog"):
            if shutil.which(candidate):
                return candidate
        return None


def run_validation(code: str, code_path: str | None = None) -> Dict[str, Any]:
    """Functional wrapper for the legacy workflow entry point.

    The original phase-1 contract only expects ``lint`` and ``syntax`` keys;
    extra keys (issues) are additive and do not break callers.
    """
    result = VerilogLinter().run(code, code_path=code_path)
    return {"lint": result["lint"], "syntax": result["syntax"], "issues": result.get("issues", [])}
