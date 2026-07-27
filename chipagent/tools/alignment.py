"""Register alignment check Tool (Phase 2 Step 5).

Verifies that the C register header and the RTL register block agree on
register names and offsets. Parses the RTL ``localparam ADDR_<NAME> = <value>``
declarations and the header ``#define <NAME>_ADDR <value>`` / ``#define <NAME>``
declarations, then reports mismatches. This is the structural spine of the
soft/hardware co-design closed loop (design spec §3.2.6 ``check_register_alignment``).
"""
from __future__ import annotations

import re
from typing import Dict, List

from .base import Tool, ToolContext, ToolResult

# localparam logic [w-1:0] ADDR_CTRL = 3'd0;  /  localparam ADDR_CTRL = 0;
_RTL_RE = re.compile(r"localparam\s+(?:logic\s+\[[\d:]+\]\s+)?ADDR_(\w+)\s*=\s*\d*'?d?(\d+)", re.I)
# #define CTRL_ADDR 0   /   #define STATUS_ADDR 1
# Convention: register-name-scoped, _ADDR suffix, NO module prefix so the
# name aligns 1:1 with the RTL ADDR_<NAME> localparams.
_HDR_RE = re.compile(r"#define\s+(\w+)_ADDR\s+(0x[0-9a-fA-F]+|\d+)")


def _normalize_value(raw: str) -> int:
    raw = raw.strip()
    if raw.lower().startswith("0x"):
        return int(raw, 16)
    return int(raw)


def _parse_rtl(code: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for m in _RTL_RE.finditer(code):
        name = m.group(1).upper()
        out[name] = _normalize_value(m.group(2))
    return out


def _parse_header(code: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for m in _HDR_RE.finditer(code):
        name = m.group(1).upper()
        try:
            out[name] = _normalize_value(m.group(2))
        except ValueError:
            continue
    return out


class RegisterAlignmentTool(Tool):
    name = "check_register_alignment"

    def run(self, ctx: ToolContext) -> ToolResult:
        rtl = ctx.inputs.get("reg_code") or ""
        header = ctx.inputs.get("header_code") or ""
        rtl_regs = _parse_rtl(rtl)
        hdr_regs = _parse_header(header)

        issues: List[str] = []
        mismatches: List[Dict[str, object]] = []
        if not rtl_regs:
            issues.append("no ADDR_<NAME> localparams found in RTL")
        if not hdr_regs:
            issues.append("no register #define found in header")

        all_names = sorted(set(rtl_regs) | set(hdr_regs))
        for name in all_names:
            r = rtl_regs.get(name)
            h = hdr_regs.get(name)
            if r is None:
                mismatches.append({"name": name, "rtl": None, "header": h, "issue": "missing in RTL"})
            elif h is None:
                mismatches.append({"name": name, "rtl": r, "header": None, "issue": "missing in header"})
            elif r != h:
                mismatches.append({"name": name, "rtl": r, "header": h, "issue": "offset mismatch"})

        aligned = not mismatches and bool(rtl_regs) and bool(hdr_regs)
        return ToolResult(
            result={"aligned": aligned, "rtl_regs": rtl_regs, "header_regs": hdr_regs,
                    "mismatches": mismatches},
            issues=issues + ([f"{len(mismatches)} mismatch(es)" for _ in [0]] if mismatches else []),
        )
