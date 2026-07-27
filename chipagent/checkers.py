"""Functional checkers (Phase 2 Step 4.4 / §3.5 校验层升级).

Phase 1's :class:`VerilogLinter` is the first gate (structural lint). These
checkers are the second and third gates the plan calls out:

- :class:`SimulationChecker` — compiles + runs the testbench (via the
  ``run_simulation`` tool), judges pass/fail, and collects a coverage figure
  (via ``analyze_coverage``).
- :class:`AlignmentChecker` — runs register-offset alignment
  (``check_register_alignment``) **and** software/hardware interface
  consistency (``check_sw_hw_interface``), then renders a readable diff with a
  repair suggestion on failure.

Each checker returns a dict with a ``status`` (``passed`` / ``failed`` /
``skipped``) and an ``advice`` field the workflow maps onto its retry /
approval branches: ``proceed`` | ``retry`` | ``approve`` | ``fail``. A
simulation failure is treated as retriable; an alignment failure is treated as
needing human approval (the diff is rarely something to auto-fix).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import Settings
from .models import TaskObject
from .tools import ToolContext, ToolLoader
from .tools.base import Tool


def _load_tool(name: str) -> Optional[Tool]:
    for t in ToolLoader().discover():
        if t.name == name:
            return t
    return None


class SimulationChecker:
    """Runs the testbench and collects pass/fail + coverage."""

    def __init__(self, sim_tool: Optional[Tool] = None, cov_tool: Optional[Tool] = None) -> None:
        self._sim = sim_tool or _load_tool("run_simulation")
        self._cov = cov_tool or _load_tool("analyze_coverage")

    def run(
        self,
        reg_code: str,
        tb_code: str,
        *,
        module_name: str = "soc_block",
        settings: Optional[Settings] = None,
        data_width: int = 32,
    ) -> Dict[str, Any]:
        if self._sim is None:
            return {"status": "skipped", "reason": "no run_simulation tool", "advice": "proceed"}
        sim = self._sim.run(ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name=module_name,
                            description="", constraints={"data_width": data_width}),
            inputs={"reg_code": reg_code, "tb_code": tb_code},
            settings=settings,
        )).result

        cov: Dict[str, Any] = {}
        if self._cov is not None:
            cov = self._cov.run(ToolContext(
                task=TaskObject(task_type="hw_sw_codesign", module_name=module_name,
                                description="", constraints={}),
                inputs={"sim_result": sim},
                settings=settings,
            )).result

        passed = sim.get("passed", "skipped")
        if passed == "passed":
            advice = "proceed"
            status = "passed"
        elif passed == "failed":
            advice = "retry"  # sim failures are retriable per §4.4
            status = "failed"
        else:
            advice = "proceed"  # skipped (no simulator) — do not block the loop
            status = "skipped"
        return {
            "status": status,
            "passed": passed,
            "sim": sim,
            "coverage": cov,
            "advice": advice,
        }


class AlignmentChecker:
    """Checks register-offset alignment + SW/HW interface consistency."""

    def __init__(self, align_tool: Optional[Tool] = None, iface_tool: Optional[Tool] = None) -> None:
        self._align = align_tool or _load_tool("check_register_alignment")
        self._iface = iface_tool or _load_tool("check_sw_hw_interface")

    def run(
        self,
        reg_code: str,
        header_code: str,
        driver_code: str = "",
        *,
        module_name: str = "soc_block",
    ) -> Dict[str, Any]:
        align_res: Dict[str, Any] = {}
        if self._align is not None:
            align_res = self._align.run(ToolContext(
                task=TaskObject(task_type="hw_sw_codesign", module_name=module_name,
                                description="", constraints={}),
                inputs={"reg_code": reg_code, "header_code": header_code},
            )).result

        iface_res: Dict[str, Any] = {}
        if self._iface is not None and driver_code:
            iface_res = self._iface.run(ToolContext(
                task=TaskObject(task_type="hw_sw_codesign", module_name=module_name,
                                description="", constraints={}),
                inputs={"reg_code": reg_code, "driver_code": driver_code,
                        "module_name": module_name},
            )).result

        aligned = bool(align_res.get("aligned")) and (
            not iface_res or bool(iface_res.get("aligned"))
        )
        diff = self._render_diff(align_res, iface_res)
        if aligned:
            status, advice = "passed", "proceed"
        elif align_res and not align_res.get("aligned"):
            # Offset mismatch is rarely auto-fixable — escalate to a human.
            status, advice = "failed", "approve"
        else:
            status, advice = "failed", "approve"
        return {
            "status": status,
            "aligned": aligned,
            "alignment": align_res,
            "interface": iface_res,
            "diff": diff,
            "advice": advice,
        }

    @staticmethod
    def _render_diff(align_res: Dict[str, Any], iface_res: Dict[str, Any]) -> str:
        lines: List[str] = []
        for mm in align_res.get("mismatches", []):
            name = mm.get("name")
            rtl = mm.get("rtl")
            hdr = mm.get("header")
            issue = mm.get("issue")
            lines.append(f"  {name}: RTL={rtl} HEADER={hdr} ({issue})")
        for ck, ok in (iface_res.get("checks") or {}).items():
            if not ok and isinstance(ok, bool):
                lines.append(f"  interface check '{ck}' failed")
        return "\n".join(lines) if lines else "(no mismatches)"
