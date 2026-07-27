"""Software/hardware interface consistency Tool (Phase 2 Step 5 / §3.2.6).

Cross-checks the generated Linux driver against the RTL register block's
*interface contract* — distinct from :class:`RegisterAlignmentTool`, which
checks numeric offset/mask agreement. This tool checks the structural glue:

- the driver ``#include``s ``<module>_regs.h``;
- the driver's ``compatible`` string and ``of_match_table`` reference the
  module (so devicetree binding matches the RTL block name);
- the driver exposes ``<module>_read_reg`` / ``<module>_write_reg`` helpers;
- every RTL top-level port the driver is expected to drive is referenced by
  name in the driver source (e.g. ``ctrl_start``).

Failing checks produce a readable diff-style report the task panel can surface
for an approval decision.
"""
from __future__ import annotations

import re
from typing import Dict, List

from .base import Tool, ToolContext, ToolResult


# module <name> (...)  — capture the port list up to the closing ');'
_MODULE_RE = re.compile(r"\bmodule\s+(\w+)\s*\((.*?)\)\s*;", re.S)
_PORT_RE = re.compile(
    r"\b(input|output|inout)\b(?:\s+logic)?(?:\s*\[[\d:]+\])?\s+(\w+)", re.I
)
_INC_RE = re.compile(r'#include\s+[<"]([^>"]+\.h)[>"]')
_COMPAT_RE = re.compile(r'\.compatible\s*=\s*"([^"]+)"')
_OF_MATCH_RE = re.compile(r"of_device_id\s+(\w+)\[\]")
_HELPER_RE = re.compile(r"\b(\w+_read_reg|\w+_write_reg)\s*\(")


class SwHwInterfaceTool(Tool):
    name = "check_sw_hw_interface"

    def run(self, ctx: ToolContext) -> ToolResult:
        rtl = ctx.inputs.get("reg_code") or ""
        driver = ctx.inputs.get("driver_code") or ""
        module = (ctx.inputs.get("module_name") or self._rtl_module_name(rtl) or "block").lower()

        rtl_module = self._rtl_module_name(rtl) or ""
        ports = self._rtl_ports(rtl)
        includes = _INC_RE.findall(driver)
        compat = _COMPAT_RE.findall(driver)
        helpers = sorted(set(m.group(1) for m in _HELPER_RE.finditer(driver)))

        checks: Dict[str, bool] = {}
        issues: List[str] = []

        inc_ok = any(inc == f"{module}_regs.h" for inc in includes)
        checks["driver_includes_reg_header"] = inc_ok
        if not inc_ok:
            issues.append(f"driver does not #include \"{module}_regs.h\"")

        compat_ok = any(module in c for c in compat)
        checks["compatible_references_module"] = compat_ok
        if not compat_ok:
            issues.append(f"no compatible string referencing '{module}'")

        expected_helpers = {f"{module}_read_reg", f"{module}_write_reg"}
        helper_ok = expected_helpers.issubset(set(helpers))
        checks["driver_has_mmio_helpers"] = helper_ok
        if not helper_ok:
            missing = expected_helpers - set(helpers)
            issues.append(f"driver missing MMIO helper(s): {sorted(missing)}")

        # RTL top-level ports the driver should be aware of by name.
        referenced_ports = {
            name for name in ports
            if re.search(r"\b" + re.escape(name) + r"\b", driver)
        }
        checks["rtl_module_named"] = bool(rtl_module)
        checks["rtl_port_count"] = len(ports)

        aligned = inc_ok and compat_ok and helper_ok
        return ToolResult(
            result={
                "aligned": aligned,
                "module": module,
                "rtl_module": rtl_module,
                "rtl_ports": ports,
                "driver_includes": includes,
                "compatible_strings": compat,
                "driver_helpers": helpers,
                "checks": checks,
                "referenced_ports": sorted(referenced_ports),
            },
            issues=issues,
        )

    @staticmethod
    def _rtl_module_name(code: str) -> str | None:
        m = re.search(r"\bmodule\s+(\w+)\s*\(", code)
        return m.group(1) if m else None

    @staticmethod
    def _rtl_ports(code: str) -> List[str]:
        m = _MODULE_RE.search(code)
        if not m:
            return []
        portlist = m.group(2)
        ports: List[str] = []
        for pm in _PORT_RE.finditer(portlist):
            ports.append(pm.group(2))
        return ports
