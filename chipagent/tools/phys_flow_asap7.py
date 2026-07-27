"""ASAP7 physical implementation flow through OpenROAD Flow Scripts."""
from __future__ import annotations

import os
import re
import json
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

from chipagent.toolchain import docker_image_status
from chipagent.tools.base import Tool, ToolContext, ToolResult, missing_tool_result, trust_metadata


class ASAP7PhysicalFlowTool(Tool):
    name = "run_physical_flow_asap7"

    def run(self, ctx: ToolContext) -> ToolResult:
        reg_code = (ctx.inputs.get("reg_code") or ctx.inputs.get("netlist") or "").strip()
        module_name = ctx.task.module_name or ctx.inputs.get("module_name") or "top"
        if not reg_code:
            return ToolResult(
                result={"status": "error", "message": "No reg_code or netlist provided"},
                issues=["Missing input: reg_code or netlist"],
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", module_name):
            return ToolResult(
                result={"status": "error", "message": f"Invalid module_name: {module_name}"},
                issues=["Invalid input: module_name"],
            )

        image = os.environ.get("CHIPAGENT_OPENROAD_IMAGE", "chipagent/openroad:latest")
        docker = docker_image_status(image)
        if not docker.get("cli_available") or not docker.get("image_available"):
            return missing_tool_result(
                "openroad",
                "OpenROAD ORFS image is unavailable. Run: bash scripts/setup_eda_env.sh --docker-openroad",
                install_url="https://openroad-flow-scripts.readthedocs.io/en/latest/user/DockerShell.html",
            )

        out = Path(ctx.inputs.get("output_dir") or Path("generated") / "physical" / module_name)
        work = out / "orfs-work"
        out.mkdir(parents=True, exist_ok=True)

        clock_port = ctx.inputs.get("clock_port") or "clk"
        clock_period = float(ctx.inputs.get("clock_period") or 310.0)
        core_utilization = int(ctx.inputs.get("core_utilization") or 10)
        place_density = float(ctx.inputs.get("place_density") or 0.20)
        manifest = _manifest(
            reg_code=reg_code,
            module_name=module_name,
            image=image,
            clock_port=clock_port,
            clock_period=clock_period,
            core_utilization=core_utilization,
            place_density=place_density,
        )
        manifest_path = out / "flow_manifest.json"
        cache_enabled = bool(ctx.inputs.get("cache", True))
        clean = bool(ctx.inputs.get("clean", False))
        if cache_enabled and not clean and _cache_valid(out, work, manifest):
            return _cached_result(out, work, module_name, manifest)

        if clean and work.exists():
            shutil.rmtree(work)

        design_src = work / "src" / module_name
        design_cfg = work / "designs" / "asap7" / module_name
        design_src.mkdir(parents=True, exist_ok=True)
        design_cfg.mkdir(parents=True, exist_ok=True)
        for subdir in ("logs", "reports", "results", "objects"):
            (work / subdir).mkdir(parents=True, exist_ok=True)

        rtl_path = design_src / f"{module_name}.v"
        sdc_path = design_cfg / "constraint.sdc"
        config_path = design_cfg / "config.mk"
        run_log = out / "orfs_run.log"
        rtl_path.write_text(reg_code + "\n", encoding="utf-8")
        sdc_path.write_text(_sdc(module_name, clock_port, clock_period), encoding="utf-8")
        config_path.write_text(
            _config(module_name, core_utilization, place_density),
            encoding="utf-8",
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{design_src.resolve()}:/OpenROAD-flow-scripts/flow/designs/src/{module_name}",
            "-v",
            f"{design_cfg.resolve()}:/OpenROAD-flow-scripts/flow/designs/asap7/{module_name}",
            "-v",
            f"{(work / 'logs').resolve()}:/OpenROAD-flow-scripts/flow/logs/asap7/{module_name}",
            "-v",
            f"{(work / 'reports').resolve()}:/OpenROAD-flow-scripts/flow/reports/asap7/{module_name}",
            "-v",
            f"{(work / 'results').resolve()}:/OpenROAD-flow-scripts/flow/results/asap7/{module_name}",
            "-v",
            f"{(work / 'objects').resolve()}:/OpenROAD-flow-scripts/flow/objects/asap7/{module_name}",
            "-w",
            "/OpenROAD-flow-scripts/flow",
            image,
            "bash",
            "-lc",
            (
                "source /OpenROAD-flow-scripts/env.sh >/dev/null && "
                f"make DESIGN_CONFIG=./designs/asap7/{module_name}/config.mk"
            ),
        ]

        timeout = int(ctx.inputs.get("timeout") or 1800)
        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            returncode = proc.returncode
            report = proc.stdout
            if proc.stderr:
                report = f"{proc.stdout}\n\n[stderr]\n{proc.stderr}"
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            report = f"{stdout}\n\n[stderr]\n{stderr}\nFlow timed out after {timeout} seconds."
        run_log.write_text(report, encoding="utf-8")

        artifacts = _collect_artifacts(out, work, module_name)
        artifacts.update({
            "rtl": str(rtl_path),
            "sdc": str(sdc_path),
            "config.mk": str(config_path),
            "run.log": str(run_log),
            "manifest.json": str(manifest_path),
        })

        status = "timeout" if timed_out else ("success" if returncode == 0 else "failed")
        diagnosis = _diagnose(report, status=status, timeout=timeout)

        return ToolResult(
            result={
                "status": status,
                "platform": "asap7",
                "module_name": module_name,
                "output_dir": str(out),
                "cached": False,
                "reproducibility": manifest,
                **trust_metadata(
                    source="tool",
                    tool="openroad-orfs",
                    tool_available=True,
                    command=cmd,
                    artifacts=artifacts,
                ),
                "qor": _collect_qor(work),
                "summary": _summarize(report),
                "diagnosis": diagnosis,
                "report_tail": "\n".join(report.splitlines()[-80:]),
            },
            issues=[] if returncode == 0 else ["OpenROAD ORFS ASAP7 flow failed"],
        )


def _config(module_name: str, core_utilization: int, place_density: float) -> str:
    return f"""export PLATFORM = asap7
export DESIGN_NAME = {module_name}

export VERILOG_FILES = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

export CORE_UTILIZATION = {core_utilization}
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 0.5
export PLACE_DENSITY = {place_density}

export SKIP_LAST_GASP ?= 1
export SYNTH_USE_SYN = 1
export SKIP_REPORT_METRICS ?= 1
export SKIP_CTS_REPAIR_TIMING ?= 1
export REMOVE_ABC_BUFFERS ?= 1
export SKIP_INCREMENTAL_REPAIR ?= 1
export GPL_TIMING_DRIVEN ?= 0
export GPL_ROUTING_DRIVEN ?= 0
"""


def _manifest(
    *,
    reg_code: str,
    module_name: str,
    image: str,
    clock_port: str,
    clock_period: float,
    core_utilization: int,
    place_density: float,
) -> Dict[str, Any]:
    payload = {
        "platform": "asap7",
        "module_name": module_name,
        "reg_code": reg_code,
        "clock_port": clock_port,
        "clock_period": clock_period,
        "core_utilization": core_utilization,
        "place_density": place_density,
        "openroad_image": image,
        "flow": "openroad-orfs",
    }
    input_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "input_hash": input_hash,
        "platform": "asap7",
        "module_name": module_name,
        "openroad_image": image,
        "parameters": {
            "clock_port": clock_port,
            "clock_period": clock_period,
            "core_utilization": core_utilization,
            "place_density": place_density,
        },
    }


def _cache_valid(out: Path, work: Path, manifest: Dict[str, Any]) -> bool:
    manifest_path = out / "flow_manifest.json"
    if not manifest_path.exists():
        return False
    existing = _read_json(manifest_path)
    if existing.get("input_hash") != manifest.get("input_hash"):
        return False
    required = [
        work / "results" / "base" / "6_final.def",
        work / "results" / "base" / "6_final.gds",
        out / "orfs_run.log",
    ]
    return all(path.exists() for path in required)


def _cached_result(out: Path, work: Path, module_name: str, manifest: Dict[str, Any]) -> ToolResult:
    artifacts = _collect_artifacts(out, work, module_name)
    artifacts.update({
        "rtl": str(work / "src" / module_name / f"{module_name}.v"),
        "sdc": str(work / "designs" / "asap7" / module_name / "constraint.sdc"),
        "config.mk": str(work / "designs" / "asap7" / module_name / "config.mk"),
        "run.log": str(out / "orfs_run.log"),
        "manifest.json": str(out / "flow_manifest.json"),
    })
    report = (out / "orfs_run.log").read_text(encoding="utf-8", errors="replace")
    return ToolResult(
        result={
            "status": "success",
            "platform": "asap7",
            "module_name": module_name,
            "output_dir": str(out),
            "cached": True,
            "reproducibility": manifest,
            **trust_metadata(
                source="tool",
                tool="openroad-orfs",
                tool_available=True,
                artifacts=artifacts,
            ),
            "qor": _collect_qor(work),
            "summary": _summarize(report),
            "diagnosis": _diagnose(report, status="success", timeout=0),
            "report_tail": "\n".join(report.splitlines()[-80:]),
        },
        issues=[],
    )


def _sdc(module_name: str, clock_port: str, clock_period: float) -> str:
    return f"""current_design {module_name}

set clk_name core_clock
set clk_port_name {clock_port}
set clk_period {clock_period}
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port
set clk_io_name vclk_$clk_name
create_clock -name $clk_io_name -period $clk_period

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay [expr $clk_period * $clk_io_pct] -clock $clk_io_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_io_name [all_outputs]
"""


def _collect_artifacts(out: Path, work: Path, module_name: str) -> Dict[str, str]:
    artifacts: Dict[str, str] = {}
    base = "base"
    roots = {
        "results": work / "results" / base,
        "reports": work / "reports" / base,
        "logs": work / "logs" / base,
    }
    for name, root in roots.items():
        if root.exists():
            artifacts[f"{name}_dir"] = str(root)

    patterns = {
        "final_def": ["6_final.def", "6_1_merged.def", "*.def"],
        "final_gds": ["6_final.gds", "6_1_merged.gds", "*.gds"],
        "final_odb": ["6_final.odb", "*.odb"],
        "route_drc": ["5_route_drc.rpt", "*drc*.rpt"],
        "synth_stat": ["synth_stat.txt", "*stat*.txt"],
    }
    for key, pats in patterns.items():
        for root in roots.values():
            for pat in pats:
                matches = sorted(root.glob(pat)) if root.exists() else []
                if matches:
                    artifacts[key] = str(matches[-1])
                    break
            if key in artifacts:
                break
    return artifacts


def _collect_qor(work: Path) -> Dict[str, Any]:
    logs = work / "logs" / "base"
    reports = work / "reports" / "base"
    final = _read_json(logs / "6_report.json")
    route = _read_json(logs / "5_2_route.json")
    place = _read_json(logs / "3_5_place_dp.json")
    cts = _read_json(logs / "4_1_cts.json")

    route_drc = reports / "5_route_drc.rpt"
    route_drc_text = route_drc.read_text(encoding="utf-8", errors="replace") if route_drc.exists() else ""

    return {
        "instance_count": final.get("finish__design__instance__count"),
        "instance_area": final.get("finish__design__instance__area"),
        "sequential_cell_count": final.get("finish__design__instance__count__class:sequential_cell"),
        "clock_buffer_count": final.get("finish__design__instance__count__class:clock_buffer"),
        "timing_repair_buffer_count": final.get("finish__design__instance__count__class:timing_repair_buffer"),
        "placement_utilization": place.get("detailedplace__utilization__before__dpl"),
        "placement_violations": place.get("detailedplace__design__violations"),
        "estimated_wirelength": cts.get("cts__route__wirelength__estimated")
        or place.get("detailedplace__route__wirelength__estimated"),
        "route_wirelength": route.get("detailedroute__route__wirelength"),
        "route_vias": route.get("detailedroute__route__vias"),
        "route_drc_errors": route.get("detailedroute__route__drc_errors"),
        "route_drc_report_bytes": len(route_drc_text.encode("utf-8")),
        "antenna_violating_nets": route.get("detailedroute__antenna__violating__nets"),
        "antenna_violating_pins": route.get("detailedroute__antenna__violating__pins"),
        "ir_drop_vdd_worst": final.get("finish__design_powergrid__drop__worst__net:VDD__corner:default"),
        "ir_drop_vss_worst": final.get("finish__design_powergrid__drop__worst__net:VSS__corner:default"),
        "flow_warnings": final.get("finish__flow__warnings__count"),
        "flow_errors": final.get("finish__flow__errors__count"),
    }


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _summarize(report: str) -> Dict[str, Any]:
    text = report.lower()
    return {
        "has_error": "error:" in text or "[error" in text,
        "has_gds": ".gds" in text,
        "line_count": len(report.splitlines()),
    }


def _diagnose(report: str, *, status: str, timeout: int) -> Dict[str, Any]:
    if status == "success":
        return {"status": "none", "stage": None, "root_cause": None, "suggested_fix": None, "evidence": []}

    evidence = _evidence_lines(report)
    stage = _failed_stage(report)
    lower = report.lower()

    if status == "timeout":
        return {
            "status": "diagnosed",
            "stage": stage,
            "root_cause": f"ASAP7 physical flow exceeded the {timeout}s timeout.",
            "suggested_fix": "Increase physical_timeout, or run a smaller design / lower effort configuration first.",
            "evidence": evidence,
        }

    if ("port" in lower and "not found" in lower) or ("get_ports" in lower and ("not found" in lower or "empty" in lower)):
        return {
            "status": "diagnosed",
            "stage": stage or "sdc",
            "root_cause": "The generated SDC clock port does not match a top-level RTL port.",
            "suggested_fix": "Pass physical_clock_port/clock_port matching the RTL clock input, for example 'clk'.",
            "evidence": evidence,
        }

    if "ifp-0065" in lower or "no rows created in the core area" in lower:
        return {
            "status": "diagnosed",
            "stage": stage or "floorplan",
            "root_cause": "Floorplan core area is too small or the synthesized design has no placeable standard-cell rows.",
            "suggested_fix": "For tiny/combinational designs, add sequential logic or lower core_utilization; for real designs, check that synthesis kept placeable instances.",
            "evidence": evidence,
        }

    if "detailedroute__route__drc_errors" in lower or "drc" in lower and "error" in lower:
        return {
            "status": "diagnosed",
            "stage": stage or "route",
            "root_cause": "Routing reported DRC errors.",
            "suggested_fix": "Lower place_density or core_utilization, then rerun the ASAP7 physical flow.",
            "evidence": evidence,
        }

    if "make:" in lower and "error" in lower:
        return {
            "status": "unknown",
            "stage": stage,
            "root_cause": "ORFS make flow failed; inspect the stage log listed in evidence.",
            "suggested_fix": "Open artifacts['run.log'] and the failed stage log; consider relaxing clock_period, lowering density/utilization, or checking RTL/SDC.",
            "evidence": evidence,
        }

    return {
        "status": "unknown",
        "stage": stage,
        "root_cause": "ASAP7 physical flow failed, but no known failure signature matched.",
        "suggested_fix": "Inspect artifacts['run.log'] and stage logs under artifacts['logs_dir'].",
        "evidence": evidence,
    }


def _failed_stage(report: str) -> str | None:
    stages = re.findall(r"Running\s+([^,\n]+),\s+stage\s+([A-Za-z0-9_]+)", report)
    if stages:
        return stages[-1][1]
    targets = re.findall(r"Makefile:\d+:\s+([^\\]\s]+)", report)
    if targets:
        return targets[-1]
    return None


def _evidence_lines(report: str, limit: int = 8) -> list[str]:
    lines = []
    patterns = ("[ERROR", "Error:", "ERROR:", "make:", "No rows created", "not found", "timed out")
    for line in report.splitlines():
        stripped = line.strip()
        if any(pattern in stripped for pattern in patterns):
            lines.append(stripped)
    return lines[-limit:]
