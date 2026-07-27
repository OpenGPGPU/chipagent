"""SystemVerilog parameter-grid DSE over the existing ChipAgent flow."""
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from chipagent.tools.base import Tool, ToolContext, ToolResult


class SVParameterDSETool(Tool):
    name = "run_sv_parameter_dse"

    def run(self, ctx: ToolContext) -> ToolResult:
        reg_code = (ctx.inputs.get("reg_code") or "").strip()
        module_name = ctx.inputs.get("module_name") or ctx.task.module_name or "dut"
        parameters = ctx.inputs.get("parameters") or {}
        if not reg_code:
            return ToolResult(
                result={"status": "error", "message": "No reg_code provided"},
                issues=["Missing input: reg_code"],
            )
        if not parameters:
            return ToolResult(
                result={"status": "error", "message": "No parameter grid provided"},
                issues=["Missing input: parameters"],
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", module_name):
            return ToolResult(
                result={"status": "error", "message": f"Invalid module_name: {module_name}"},
                issues=["Invalid input: module_name"],
            )

        grid = _expand_grid(parameters)
        max_candidates = int(ctx.inputs.get("max_candidates") or 32)
        if len(grid) > max_candidates:
            return ToolResult(
                result={
                    "status": "error",
                    "message": f"Parameter grid has {len(grid)} candidates; limit is {max_candidates}",
                    "candidate_count": len(grid),
                    "max_candidates": max_candidates,
                },
                issues=["Too many DSE candidates"],
            )

        from chipagent.mcp import chipagent_run_flow

        output_dir = Path(ctx.inputs.get("output_dir") or Path("generated") / "sv_dse" / module_name)
        tb_code = ctx.inputs.get("tb_code")
        run_physical = bool(ctx.inputs.get("run_physical", False))
        candidates = []
        for idx, params in enumerate(grid):
            candidate_name = _candidate_name(module_name, idx, params)
            candidate_dir = output_dir / candidate_name
            wrapper = _wrapper(reg_code, module_name, candidate_name, params)
            raw = chipagent_run_flow(
                reg_code=wrapper,
                tb_code=tb_code,
                module_name=candidate_name,
                output_dir=str(candidate_dir),
                run_formality=False,
                run_physical=run_physical,
                physical_clock_port=ctx.inputs.get("physical_clock_port") or "clk",
                physical_clock_period=float(ctx.inputs.get("physical_clock_period") or 310.0),
                physical_timeout=int(ctx.inputs.get("physical_timeout") or 1800),
            )
            flow = json.loads(raw)
            candidates.append(_candidate_result(candidate_name, params, flow))

        ranked = sorted(candidates, key=_rank_key)
        best = ranked[0] if ranked else None
        report = {
            "status": "success" if candidates else "error",
            "module_name": module_name,
            "candidate_count": len(candidates),
            "run_physical": run_physical,
            "best": best,
            "candidates": ranked,
            "output_dir": str(output_dir),
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"{module_name}_sv_dse_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["artifacts"] = {"dse_report.json": str(report_path)}
        return ToolResult(result=report, issues=[])


def _expand_grid(parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
    keys = sorted(parameters)
    values = []
    for key in keys:
        item = parameters[key]
        if not isinstance(item, list):
            item = [item]
        values.append(item)
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def _candidate_name(module_name: str, idx: int, params: Dict[str, Any]) -> str:
    suffix = "_".join(f"{_sanitize(k)}{_sanitize(v)}" for k, v in sorted(params.items()))
    return f"{module_name}_dse_{idx}_{suffix}"


def _sanitize(value: Any) -> str:
    text = str(value)
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


def _wrapper(reg_code: str, module_name: str, candidate_name: str, params: Dict[str, Any]) -> str:
    ports = _module_ports(reg_code, module_name)
    port_names = _port_names(ports)
    param_defaults = ",\n  ".join(
        f"parameter {key} = {_verilog_value(value)}" for key, value in sorted(params.items())
    )
    overrides = ",\n    ".join(f".{key}({key})" for key in sorted(params))
    connections = ",\n    ".join(f".{name}({name})" for name in port_names)
    inst = f"{module_name} #(\n    {overrides}\n  ) u_impl (\n    {connections}\n  );"
    if ports:
        wrapper_module = f"module {candidate_name} #(\n  {param_defaults}\n)(\n  {ports}\n);\n  {inst}\nendmodule\n"
    else:
        wrapper_module = f"module {candidate_name} #(\n  {param_defaults}\n);\n  {inst}\nendmodule\n"
    return f"{reg_code.rstrip()}\n\n{wrapper_module}"


def _module_ports(reg_code: str, module_name: str) -> str:
    m = re.search(rf"\bmodule\s+{re.escape(module_name)}\s*(?:#\s*\(.*?\)\s*)?\((.*?)\)\s*;", reg_code, re.S)
    if not m:
        return ""
    return " ".join(m.group(1).split())


def _port_names(ports: str) -> List[str]:
    names = []
    for item in _split_ports(ports):
        item = item.strip()
        if not item:
            continue
        item = item.split("=")[0].strip()
        m = re.search(r"([A-Za-z_][A-Za-z0-9_$]*)\s*$", item)
        if m:
            names.append(m.group(1))
    return names


def _split_ports(ports: str) -> List[str]:
    parts = []
    depth = 0
    start = 0
    for idx, ch in enumerate(ports):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(ports[start:idx])
            start = idx + 1
    parts.append(ports[start:])
    return parts


def _verilog_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def _candidate_result(name: str, params: Dict[str, Any], flow: Dict[str, Any]) -> Dict[str, Any]:
    summary = flow.get("summary") or {}
    synthesis = summary.get("synthesis") or {}
    physical = summary.get("physical") or {}
    qor = physical.get("qor") or {}
    failed = flow.get("status") != "success"
    score = _score(failed=failed, cells=synthesis.get("cells"), qor=qor)
    return {
        "name": name,
        "params": params,
        "status": flow.get("status"),
        "failures": flow.get("failures") or [],
        "cells": synthesis.get("cells"),
        "physical": {
            "status": physical.get("status"),
            "cached": physical.get("cached"),
            "qor": qor,
            "has_gds": physical.get("has_gds"),
        },
        "score": score,
        "artifacts": flow.get("summary", {}).get("artifacts") or {},
        "flow_report": (flow.get("artifacts") or {}).get("flow_report.json"),
        "flow_summary_html": (flow.get("artifacts") or {}).get("flow_summary.html"),
    }


def _score(*, failed: bool, cells: Any, qor: Dict[str, Any]) -> float:
    if failed:
        return -1.0
    area = qor.get("instance_area")
    route_drc = qor.get("route_drc_errors")
    if area is not None:
        base = 1.0 / (1.0 + float(area))
    elif cells is not None:
        base = 1.0 / (1.0 + float(cells))
    else:
        base = 0.0
    if route_drc:
        base -= min(0.5, 0.05 * float(route_drc))
    return round(base, 6)


def _rank_key(candidate: Dict[str, Any]) -> tuple:
    return (-candidate.get("score", -1), candidate.get("cells") or 10**12, candidate["name"])
