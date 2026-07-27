"""HW/SW co-simulation stub Tool (Phase 2 §3.2.6 / §4.3C).

A placeholder co-simulation interface that records the intent and returns a
stub result. The design spec §3.2.6 explicitly allows Phase 2 to ship an
interface stub without a real DPI connection:

    "协同仿真接口预留（run_sw_hw_cosim，Phase 2 只做接口与 stub，不接真实 DPI）"

Inputs (``ctx.inputs``):
  - ``reg_code``     : RTL register block code (SV)
  - ``driver_code``  : Linux driver code (C)
  - ``test_scenario``: optional description of the test scenario

Output: a structured stub result indicating the co-simulation intent,
        parameters captured, and a note that real DPI is not yet connected.
        This keeps the MCP tool surface complete so a future phase can swap
        in a real co-simulation backend without changing callers.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from .base import Tool, ToolContext, ToolResult


class SwHwCosimTool(Tool):
    name = "run_sw_hw_cosim"

    def run(self, ctx: ToolContext) -> ToolResult:
        reg_code: str = ctx.inputs.get("reg_code", "")
        driver_code: str = ctx.inputs.get("driver_code", "")
        test_scenario: str = ctx.inputs.get("test_scenario", "")

        # Extract register names from RTL for traceability.
        reg_names = self._extract_reg_names(reg_code)
        # Extract function names from the driver.
        drv_funcs = self._extract_func_names(driver_code)

        # Validate that the driver references registers defined in the RTL.
        referenced = [r for r in reg_names if r in driver_code]
        unreferenced = [r for r in reg_names if r not in driver_code]

        return ToolResult(
            result={
                "status": "stub",
                "note": "co-simulation DPI backend not connected (Phase 2 interface stub)",
                "registers_detected": len(reg_names),
                "register_names": reg_names[:20],
                "driver_functions": drv_funcs[:20],
                "registers_referenced_by_driver": referenced[:20],
                "registers_unreferenced": unreferenced[:20],
                "test_scenario": test_scenario or "(not specified)",
                "dpi_ready": False,
            },
            issues=[] if referenced else [
                "no register cross-references found between RTL and driver"
            ],
        )

    @staticmethod
    def _extract_reg_names(reg_code: str) -> list[str]:
        """Pull register signal names from the RTL code."""
        names: list[str] = []
        for m in re.finditer(
            r"(?:logic|reg|wire)\s+(?:\[[^\]]*\]\s+)?(\w+)", reg_code
        ):
            name = m.group(1)
            if name not in ("clk", "rst_n", "reset", "rst"):
                names.append(name)
        return sorted(set(names))

    @staticmethod
    def _extract_func_names(driver_code: str) -> list[str]:
        """Pull C function names from the driver code."""
        names: list[str] = []
        for m in re.finditer(
            r"^(?:static\s+)?(?:int|void|ssize_t|long|unsigned)\s+(\w+)\s*\(",
            driver_code, re.MULTILINE,
        ):
            names.append(m.group(1))
        return sorted(set(names))
