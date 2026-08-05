"""ASAP7 physical implementation flow through OpenROAD Flow Scripts."""
from __future__ import annotations

import os
import re
import json
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from chipagent.toolchain import configured_openroad_image, docker_image_status
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

        image = configured_openroad_image()
        docker = docker_image_status(image)
        if not docker.get("cli_available") or not docker.get("usable"):
            return missing_tool_result(
                "openroad",
                "OpenROAD ORFS image is unavailable or has the wrong architecture. "
                f"{docker.get('error') or ''} Run: bash scripts/setup_eda_env.sh --docker-openroad",
                install_url="https://openroad-flow-scripts.readthedocs.io/en/latest/user/DockerShell.html",
            )

        requested_output = ctx.inputs.get("output_dir")
        out = (
            Path(requested_output)
            if requested_output
            else _next_numbered_output(Path("generated") / "physical", module_name)
        )
        work = out / "orfs-work"
        out.mkdir(parents=True, exist_ok=True)

        clock_port = ctx.inputs.get("clock_port") or "clk"
        clock_period = float(ctx.inputs.get("clock_period") or 310.0)
        core_utilization = int(ctx.inputs.get("core_utilization") or 10)
        place_density = float(ctx.inputs.get("place_density") or 0.20)
        corner = str(ctx.inputs.get("corner") or "WC").upper()
        cell_vt = str(ctx.inputs.get("cell_vt") or "RVT").upper()
        if cell_vt not in {"RVT", "LVT", "SLVT"}:
            return ToolResult(
                result={"status": "error", "message": f"Invalid ASAP7 cell_vt: {cell_vt}"},
                issues=["Invalid input: cell_vt (expected RVT, LVT, or SLVT)"],
            )
        timing_effort = str(ctx.inputs.get("timing_effort") or "explore").lower()
        if timing_effort not in {"explore", "closure", "closure_no_cts"}:
            return ToolResult(
                result={
                    "status": "error",
                    "message": f"Invalid timing_effort: {timing_effort}",
                },
                issues=[
                    "Invalid input: timing_effort "
                    "(expected explore, closure, or closure_no_cts)"
                ],
            )
        synthesis_engine = str(
            ctx.inputs.get("synthesis_engine") or "syn"
        ).lower()
        if synthesis_engine not in {"syn", "yosys"}:
            return ToolResult(
                result={
                    "status": "error",
                    "message": f"Invalid synthesis_engine: {synthesis_engine}",
                },
                issues=[
                    "Invalid input: synthesis_engine (expected syn or yosys)"
                ],
            )
        sv_frontend = str(ctx.inputs.get("sv_frontend") or "native").lower()
        if sv_frontend not in {"native", "sv2v"}:
            return ToolResult(
                result={"status": "error", "message": f"Invalid sv_frontend: {sv_frontend}"},
                issues=["Invalid input: sv_frontend (expected native or sv2v)"],
            )
        enable_retiming = bool(ctx.inputs.get("enable_retiming", False))
        swap_arithmetic_operators = bool(
            ctx.inputs.get("swap_arithmetic_operators", False)
        )
        generate_gds = bool(ctx.inputs.get("generate_gds", True))
        max_fanout_raw = ctx.inputs.get("max_fanout")
        max_fanout = int(max_fanout_raw) if max_fanout_raw is not None else None
        if max_fanout is not None and max_fanout < 2:
            return ToolResult(
                result={"status": "error", "message": "max_fanout must be at least 2"},
                issues=["Invalid input: max_fanout"],
            )
        setup_slack_margin = float(ctx.inputs.get("setup_slack_margin") or 0.0)
        abc_clock_period_raw = ctx.inputs.get("abc_clock_period_ps")
        abc_clock_period_ps = (
            float(abc_clock_period_raw)
            if abc_clock_period_raw is not None
            else None
        )
        if abc_clock_period_ps is not None and abc_clock_period_ps <= 0:
            return ToolResult(
                result={
                    "status": "error",
                    "message": "abc_clock_period_ps must be positive",
                },
                issues=["Invalid input: abc_clock_period_ps"],
            )
        if corner not in {"BC", "TC", "WC"}:
            return ToolResult(
                result={"status": "error", "message": f"Invalid ASAP7 corner: {corner}"},
                issues=["Invalid input: corner (expected BC, TC, or WC)"],
            )
        try:
            macro_lefs = _macro_files(ctx.inputs.get("macro_lefs"), ".lef")
            macro_libs = _macro_files(ctx.inputs.get("macro_libs"), ".lib")
            macro_gds = _macro_files(ctx.inputs.get("macro_gds"), ".gds")
            placement_files = _macro_files(
                ctx.inputs.get("macro_placement_tcl"), ".tcl"
            )
        except ValueError as exc:
            return ToolResult(
                result={"status": "error", "message": str(exc)},
                issues=["Invalid macro collateral"],
            )
        if bool(macro_lefs) != bool(macro_libs):
            return ToolResult(
                result={
                    "status": "error",
                    "message": "macro_lefs and macro_libs must both be provided",
                },
                issues=["Incomplete macro collateral"],
            )
        macro_placement_tcl = placement_files[0] if placement_files else None
        if len(placement_files) > 1:
            return ToolResult(
                result={"status": "error", "message": "only one macro placement Tcl is supported"},
                issues=["Invalid macro placement"],
            )
        manifest = _manifest(
            reg_code=reg_code,
            module_name=module_name,
            image=image,
            clock_port=clock_port,
            clock_period=clock_period,
            core_utilization=core_utilization,
            place_density=place_density,
            corner=corner,
            cell_vt=cell_vt,
            timing_effort=timing_effort,
            synthesis_engine=synthesis_engine,
            sv_frontend=sv_frontend,
            enable_retiming=enable_retiming,
            swap_arithmetic_operators=swap_arithmetic_operators,
            generate_gds=generate_gds,
            max_fanout=max_fanout,
            setup_slack_margin=setup_slack_margin,
            abc_clock_period_ps=abc_clock_period_ps,
            macro_lefs=macro_lefs,
            macro_libs=macro_libs,
            macro_gds=macro_gds,
            macro_placement_tcl=macro_placement_tcl,
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
        macro_dir = design_cfg / "macros"
        if macro_lefs:
            macro_dir.mkdir(parents=True, exist_ok=True)
            for macro_file in [*macro_lefs, *macro_libs, *macro_gds]:
                shutil.copy2(macro_file, macro_dir / macro_file.name)
        if macro_placement_tcl:
            shutil.copy2(macro_placement_tcl, design_cfg / "macro_placement.tcl")
        for subdir in ("logs", "reports", "results", "objects"):
            (work / subdir).mkdir(parents=True, exist_ok=True)

        # The API accepts SystemVerilog RTL (for example always_comb/logic).
        # ORFS selects its parser standard from the source extension, so writing
        # this payload as .v incorrectly forces Verilog-2005 elaboration.
        rtl_path = design_src / f"{module_name}.sv"
        sdc_path = design_cfg / "constraint.sdc"
        config_path = design_cfg / "config.mk"
        run_log = out / "orfs_run.log"
        rtl_path.write_text(reg_code + "\n", encoding="utf-8")
        sdc_path.write_text(
            _sdc(module_name, clock_port, clock_period, max_fanout=max_fanout),
            encoding="utf-8",
        )
        config_path.write_text(
            _config(
                module_name,
                core_utilization,
                place_density,
                corner,
                cell_vt=cell_vt,
                has_macros=bool(macro_lefs),
                has_macro_gds=bool(macro_gds),
                has_macro_placement=bool(macro_placement_tcl),
                timing_effort=timing_effort,
                synthesis_engine=synthesis_engine,
                sv_frontend=sv_frontend,
                enable_retiming=enable_retiming,
                swap_arithmetic_operators=swap_arithmetic_operators,
                setup_slack_margin=setup_slack_margin,
                abc_clock_period_ps=abc_clock_period_ps,
            ),
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
                + (
                    f"sv2v /OpenROAD-flow-scripts/flow/designs/src/{module_name}/{module_name}.sv "
                    f"> /OpenROAD-flow-scripts/flow/designs/src/{module_name}/{module_name}.sv2v.v && "
                    if sv_frontend == "sv2v"
                    else ""
                )
                + f"make DESIGN_CONFIG=./designs/asap7/{module_name}/config.mk"
                + ("" if generate_gds else " GDS_FINAL_FILE=")
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
        if sv_frontend == "sv2v":
            artifacts["lowered_rtl"] = str(design_src / f"{module_name}.sv2v.v")

        qor = _collect_qor(work)
        gds_export_only_failure = (
            not timed_out
            and returncode != 0
            and _is_gds_export_failure(report)
            and qor.get("setup_worst_slack_ps") is not None
            and qor.get("hold_worst_slack_ps") is not None
            and Path(artifacts.get("final_def", "")).is_file()
            and Path(artifacts.get("final_odb", "")).is_file()
        )
        status = (
            "timeout" if timed_out
            else "success" if returncode == 0
            else "partial" if gds_export_only_failure
            else "failed"
        )
        diagnosis = _diagnose(report, status=status, timeout=timeout)
        overview = _build_overview(
            status=status,
            manifest=manifest,
            qor=qor,
            diagnosis=diagnosis,
        )
        simple_report = out / "SUMMARY.md"
        simple_report.write_text(_summary_markdown(module_name, overview, artifacts), encoding="utf-8")
        artifacts["simple_report"] = str(simple_report)

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
                "overview": overview,
                "qor": qor,
                "summary": _summarize(report),
                "diagnosis": diagnosis,
                "report_tail": "\n".join(report.splitlines()[-80:]),
            },
            issues=(
                [] if returncode == 0
                else ["GDS export failed after successful post-route analysis"]
                if gds_export_only_failure
                else ["OpenROAD ORFS ASAP7 flow failed"]
            ),
        )


def _config(
    module_name: str,
    core_utilization: int,
    place_density: float,
    corner: str = "WC",
    cell_vt: str = "RVT",
    has_macros: bool = False,
    has_macro_gds: bool = False,
    has_macro_placement: bool = False,
    timing_effort: str = "explore",
    synthesis_engine: str = "syn",
    sv_frontend: str = "native",
    enable_retiming: bool = False,
    swap_arithmetic_operators: bool = False,
    setup_slack_margin: float = 0.0,
    abc_clock_period_ps: float | None = None,
) -> str:
    macro_config = ""
    if has_macros:
        macro_config = f"""
export ADDITIONAL_LEFS = $(sort $(wildcard $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/macros/*.lef))
export ADDITIONAL_LIBS = $(sort $(wildcard $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/macros/*.lib))
{('export ADDITIONAL_GDS = $(sort $(wildcard $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/macros/*.gds))' if has_macro_gds else 'export GDS_ALLOW_EMPTY = fakeram.* srambank_.*')}
{('export MACRO_PLACEMENT_TCL = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/macro_placement.tcl' if has_macro_placement else '')}
"""
    if timing_effort in {"closure", "closure_no_cts"}:
        skip_cts_repair = 1 if timing_effort == "closure_no_cts" else 0
        timing_config = f"""export SKIP_LAST_GASP = 0
export SKIP_CTS_REPAIR_TIMING = {skip_cts_repair}
export REMOVE_ABC_BUFFERS = 0
export SKIP_INCREMENTAL_REPAIR = 0
export GPL_TIMING_DRIVEN = 1
export TNS_END_PERCENT = 100
export SETUP_SLACK_MARGIN = {setup_slack_margin}
"""
    else:
        timing_config = """export SKIP_LAST_GASP = 1
export SKIP_CTS_REPAIR_TIMING = 1
export REMOVE_ABC_BUFFERS = 1
export SKIP_INCREMENTAL_REPAIR = 1
export GPL_TIMING_DRIVEN = 0
"""
    abc_config = (
        f"export ABC_CLOCK_PERIOD_IN_PS = {abc_clock_period_ps}\n"
        if abc_clock_period_ps is not None
        else ""
    )
    synth_use_syn = 1 if synthesis_engine == "syn" else 0
    retiming_config = (
        f"export SYNTH_RETIME_MODULES = {module_name}\n"
        if enable_retiming
        else ""
    )
    arithmetic_config = (
        "export OPENROAD_HIERARCHICAL = 1\n"
        "export SWAP_ARITH_OPERATORS = 1\n"
        "export SYNTH_WRAPPED_ADDERS = KOGGE_STONE,HAN_CARLSON,SKLANSKY,BRENT_KUNG\n"
        "export SYNTH_WRAPPED_MULTIPLIERS = BOOTH,BASE\n"
        if swap_arithmetic_operators
        else ""
    )
    verilog_files = (
        "$(DESIGN_HOME)/src/$(DESIGN_NAME)/$(DESIGN_NAME).sv2v.v"
        if sv_frontend == "sv2v"
        else "$(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v) $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.sv))"
    )
    return f"""export PLATFORM = asap7
export DESIGN_NAME = {module_name}
export CORNER = {corner}
export ASAP7_USE_VT = {cell_vt}
{macro_config}

export VERILOG_FILES = {verilog_files}
export SDC_FILE = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

export CORE_UTILIZATION = {core_utilization}
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 0.5
export PLACE_DENSITY = {place_density}

export SYNTH_USE_SYN = {synth_use_syn}
{retiming_config}{arithmetic_config}{abc_config}export SKIP_REPORT_METRICS = 0
{timing_config}
"""


def _macro_files(value: Any, suffix: str) -> List[Path]:
    if value is None:
        return []
    raw = [value] if isinstance(value, (str, Path)) else list(value)
    files: List[Path] = []
    names = set()
    for item in raw:
        path = Path(item).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Macro file does not exist: {path}")
        if path.suffix.lower() != suffix:
            raise ValueError(f"Expected a {suffix} macro file: {path}")
        if path.name in names:
            raise ValueError(f"Duplicate macro filename: {path.name}")
        names.add(path.name)
        files.append(path)
    return files


def _next_numbered_output(parent: Path, name: str) -> Path:
    """Allocate a human-sortable run directory such as ``003_DecodePipe``."""
    parent.mkdir(parents=True, exist_ok=True)
    highest = 0
    pattern = re.compile(r"^(\d{3})_")
    for child in parent.iterdir():
        if not child.is_dir():
            continue
        match = pattern.match(child.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return parent / f"{highest + 1:03d}_{name}"


def _manifest(
    *,
    reg_code: str,
    module_name: str,
    image: str,
    clock_port: str,
    clock_period: float,
    core_utilization: int,
    place_density: float,
    corner: str = "WC",
    cell_vt: str = "RVT",
    timing_effort: str = "explore",
    synthesis_engine: str = "syn",
    sv_frontend: str = "native",
    enable_retiming: bool = False,
    swap_arithmetic_operators: bool = False,
    generate_gds: bool = True,
    max_fanout: int | None = None,
    setup_slack_margin: float = 0.0,
    abc_clock_period_ps: float | None = None,
    macro_lefs: List[Path] | None = None,
    macro_libs: List[Path] | None = None,
    macro_gds: List[Path] | None = None,
    macro_placement_tcl: Path | None = None,
) -> Dict[str, Any]:
    macro_files = [*(macro_lefs or []), *(macro_libs or []), *(macro_gds or [])]
    if macro_placement_tcl:
        macro_files.append(macro_placement_tcl)
    macro_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in macro_files
    }
    payload = {
        "platform": "asap7",
        "module_name": module_name,
        "reg_code": reg_code,
        "clock_port": clock_port,
        "clock_period": clock_period,
        "core_utilization": core_utilization,
        "place_density": place_density,
        "corner": corner,
        "cell_vt": cell_vt,
        "timing_effort": timing_effort,
        "synthesis_engine": synthesis_engine,
        "sv_frontend": sv_frontend,
        "enable_retiming": enable_retiming,
        "swap_arithmetic_operators": swap_arithmetic_operators,
        "generate_gds": generate_gds,
        "max_fanout": max_fanout,
        "setup_slack_margin": setup_slack_margin,
        "abc_clock_period_ps": abc_clock_period_ps,
        "openroad_image": image,
        "flow": "openroad-orfs",
        "macro_hashes": macro_hashes,
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
            "corner": corner,
            "cell_vt": cell_vt,
            "timing_effort": timing_effort,
            "synthesis_engine": synthesis_engine,
            "sv_frontend": sv_frontend,
            "enable_retiming": enable_retiming,
            "swap_arithmetic_operators": swap_arithmetic_operators,
            "generate_gds": generate_gds,
            "max_fanout": max_fanout,
            "setup_slack_margin": setup_slack_margin,
            "abc_clock_period_ps": abc_clock_period_ps,
            "macros": sorted(macro_hashes),
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
        out / "orfs_run.log",
    ]
    if (manifest.get("parameters") or {}).get("generate_gds", True):
        required.append(work / "results" / "base" / "6_final.gds")
    return all(path.exists() for path in required)


def _cached_result(out: Path, work: Path, module_name: str, manifest: Dict[str, Any]) -> ToolResult:
    artifacts = _collect_artifacts(out, work, module_name)
    artifacts.update({
        "rtl": str(work / "src" / module_name / f"{module_name}.sv"),
        "sdc": str(work / "designs" / "asap7" / module_name / "constraint.sdc"),
        "config.mk": str(work / "designs" / "asap7" / module_name / "config.mk"),
        "run.log": str(out / "orfs_run.log"),
        "manifest.json": str(out / "flow_manifest.json"),
    })
    report = (out / "orfs_run.log").read_text(encoding="utf-8", errors="replace")
    qor = _collect_qor(work)
    diagnosis = _diagnose(report, status="success", timeout=0)
    overview = _build_overview(
        status="success",
        manifest=manifest,
        qor=qor,
        diagnosis=diagnosis,
    )
    simple_report = out / "SUMMARY.md"
    simple_report.write_text(_summary_markdown(module_name, overview, artifacts), encoding="utf-8")
    artifacts["simple_report"] = str(simple_report)
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
            "overview": overview,
            "qor": qor,
            "summary": _summarize(report),
            "diagnosis": diagnosis,
            "report_tail": "\n".join(report.splitlines()[-80:]),
        },
        issues=[],
    )


def _sdc(
    module_name: str,
    clock_port: str,
    clock_period: float,
    max_fanout: int | None = None,
) -> str:
    fanout_constraint = (
        f"set_max_fanout {max_fanout} [current_design]\n"
        if max_fanout is not None
        else ""
    )
    return f"""current_design {module_name}

set clk_name core_clock
set clk_port_name {clock_port}
set clk_period {clock_period}
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port
set clk_io_name vclk_$clk_name
create_clock -name $clk_io_name -period $clk_period

{fanout_constraint}
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
    finish_report = reports / "6_finish.rpt"
    critical_path = _parse_critical_path(
        finish_report.read_text(encoding="utf-8", errors="replace")
        if finish_report.exists()
        else ""
    )

    return {
        "instance_count": final.get("finish__design__instance__count"),
        "instance_area": final.get("finish__design__instance__area"),
        "sequential_cell_count": final.get("finish__design__instance__count__class:sequential_cell"),
        "clock_buffer_count": final.get("finish__design__instance__count__class:clock_buffer"),
        "timing_repair_buffer_count": final.get("finish__design__instance__count__class:timing_repair_buffer"),
        "setup_tns_ps": final.get("finish__timing__setup__tns"),
        "hold_tns_ps": final.get("finish__timing__hold__tns"),
        "setup_worst_slack_ps": final.get("finish__timing__setup__ws"),
        "hold_worst_slack_ps": final.get("finish__timing__hold__ws"),
        "setup_violation_count": final.get("finish__timing__drv__setup_violation_count"),
        "hold_violation_count": final.get("finish__timing__drv__hold_violation_count"),
        "core_clock_fmax_mhz": _hz_to_mhz(
            final.get("finish__timing__fmax__clock:core_clock")
        ),
        "virtual_io_clock_fmax_mhz": _hz_to_mhz(
            final.get("finish__timing__fmax__clock:vclk_core_clock")
        ),
        # ORFS' aggregate fmax is the maximum over every clock group.  Our SDC
        # also creates vclk_core_clock for I/O delay constraints, so the
        # aggregate can misleadingly report that virtual clock instead of the
        # implemented core clock.  Prefer the real core clock and retain the
        # virtual value in its explicitly named field above.
        "reported_fmax_mhz": _hz_to_mhz(
            final.get("finish__timing__fmax__clock:core_clock")
            or final.get("finish__timing__fmax")
        ),
        "setup_clock_skew_ps": final.get("finish__clock__skew__setup"),
        "hold_clock_skew_ps": final.get("finish__clock__skew__hold"),
        "total_power_w": final.get("finish__power__total"),
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
        "critical_path": critical_path,
    }


_OUTPUT_PINS = {
    "Y", "Q", "QN", "S", "SN", "CO", "CON", "Z", "ZN", "O", "ON"
}


def _parse_critical_path(report: str) -> Dict[str, Any] | None:
    """Break down the worst real-clock setup path from an OpenSTA report.

    OpenSTA reports interconnect delay on the destination input-pin row and
    cell delay on the output-pin row.  Keeping these separate tells us whether
    another placement pass can plausibly help or whether RTL/logic depth is the
    dominant problem.
    """
    paths: List[Dict[str, Any]] = []
    header = re.compile(
        r"^Startpoint:\s*(?P<start>[^\n]+)\n.*?"
        r"^Endpoint:\s*(?P<end>[^\n]+)\n.*?"
        r"^Path Group:\s*(?P<group>\S+)\n"
        r"^Path Type:\s*(?P<type>\S+)\n(?P<body>.*?^\s*[-\d.]+\s+slack \((?:VIOLATED|MET)\)\s*$)",
        re.MULTILINE | re.DOTALL,
    )
    for match in header.finditer(report):
        if match.group("type") != "max" or match.group("group") != "core_clock":
            continue
        body = match.group("body")
        slack_match = re.search(r"^\s*([-\d.]+)\s+slack ", body, re.MULTILINE)
        arrival_match = re.search(r"^\s*([-\d.]+)\s+data arrival time\s*$", body, re.MULTILINE)
        if not slack_match:
            continue
        paths.append({
            "startpoint": match.group("start").strip(),
            "endpoint": match.group("end").strip(),
            "slack_ps": float(slack_match.group(1)),
            "arrival_ps": float(arrival_match.group(1)) if arrival_match else None,
            "body": body,
        })
    if not paths:
        return None

    path = min(paths, key=lambda item: item["slack_ps"])
    start_name = path["startpoint"].split()[0]
    started = False
    cell_delay = 0.0
    net_delay = 0.0
    cell_count = 0
    net_count = 0
    cell_types: Dict[str, Dict[str, float | int]] = {}

    for line in path["body"].splitlines():
        if started and "data arrival time" in line:
            break
        marker = re.search(r"\s[\^v]\s", line)
        if not marker:
            continue
        numeric = re.findall(r"-?\d+(?:\.\d+)?", line[:marker.start()])
        if len(numeric) < 2:
            continue
        delay = float(numeric[-2])
        desc = line[marker.end():].strip()
        pin_match = re.search(r"/([^/\s]+)\s+\(([^()]+)\)\s*$", desc)
        if not pin_match:
            continue
        pin, cell_type = pin_match.groups()
        is_output = pin in _OUTPUT_PINS
        if not started:
            started = desc.startswith(start_name + "/") and is_output
            if not started:
                continue
        if is_output:
            cell_delay += delay
            cell_count += 1
            bucket = cell_types.setdefault(cell_type, {"count": 0, "delay_ps": 0.0})
            bucket["count"] = int(bucket["count"]) + 1
            bucket["delay_ps"] = float(bucket["delay_ps"]) + delay
        else:
            net_delay += delay
            net_count += 1

    total = cell_delay + net_delay
    dominant = sorted(
        (
            {"cell_type": name, "count": values["count"], "delay_ps": round(float(values["delay_ps"]), 3)}
            for name, values in cell_types.items()
        ),
        key=lambda item: item["delay_ps"],
        reverse=True,
    )[:8]
    return {
        "startpoint": path["startpoint"],
        "endpoint": path["endpoint"],
        "slack_ps": path["slack_ps"],
        "arrival_ps": path["arrival_ps"],
        "data_path_delay_ps": round(total, 3),
        "cell_delay_ps": round(cell_delay, 3),
        "net_delay_ps": round(net_delay, 3),
        "cell_delay_percent": round(cell_delay * 100.0 / total, 2) if total else None,
        "net_delay_percent": round(net_delay * 100.0 / total, 2) if total else None,
        "cell_count": cell_count,
        "net_count": net_count,
        "dominant_cell_types": dominant,
    }


def _hz_to_mhz(value: Any) -> float | None:
    try:
        return float(value) / 1_000_000.0
    except (TypeError, ValueError):
        return None


def _build_overview(
    *,
    status: str,
    manifest: Dict[str, Any],
    qor: Dict[str, Any],
    diagnosis: Dict[str, Any],
) -> Dict[str, Any]:
    params = manifest.get("parameters") or {}
    period_ps = _number(params.get("clock_period"))
    target_mhz = (1_000_000.0 / period_ps) if period_ps and period_ps > 0 else None
    setup_slack = _number(qor.get("setup_worst_slack_ps"))
    hold_slack = _number(qor.get("hold_worst_slack_ps"))
    setup_violations = _number(qor.get("setup_violation_count"))
    hold_violations = _number(qor.get("hold_violation_count"))
    drc_errors = _number(qor.get("route_drc_errors"))
    timing_available = setup_slack is not None and hold_slack is not None

    if status not in {"success", "partial"}:
        verdict = "FAIL"
        headline = f"Physical flow failed at {diagnosis.get('stage') or 'an unknown stage'}."
    elif not timing_available:
        verdict = "UNKNOWN"
        headline = "Layout completed, but post-route timing metrics are unavailable."
    else:
        passed = (
            setup_slack >= 0
            and hold_slack >= 0
            and (setup_violations in {None, 0})
            and (hold_violations in {None, 0})
            and (drc_errors in {None, 0})
        )
        verdict = "PASS" if passed else "FAIL"
        target_text = f"{target_mhz:.1f} MHz" if target_mhz is not None else "the target clock"
        headline = (
            f"Post-route design meets {target_text} at the {params.get('corner', 'unspecified')} corner."
            if passed
            else f"Post-route design does not meet {target_text} at the {params.get('corner', 'unspecified')} corner."
        )
        if status == "partial":
            headline += " DEF/ODB and PPA are valid, but final GDS export failed."

    return {
        "verdict": verdict,
        "headline": headline,
        "target": {
            "clock_period_ps": period_ps,
            "frequency_mhz": target_mhz,
            "corner": params.get("corner"),
        },
        "timing": {
            "setup_slack_ps": setup_slack,
            "hold_slack_ps": hold_slack,
            "setup_tns_ps": qor.get("setup_tns_ps"),
            "hold_tns_ps": qor.get("hold_tns_ps"),
            "core_clock_fmax_mhz": qor.get("core_clock_fmax_mhz"),
            "setup_violations": qor.get("setup_violation_count"),
            "hold_violations": qor.get("hold_violation_count"),
            "critical_path": qor.get("critical_path"),
        },
        "physical": {
            "drc_errors": qor.get("route_drc_errors"),
            "antenna_violating_nets": qor.get("antenna_violating_nets"),
            "standard_cell_area_um2": qor.get("instance_area"),
            "placement_utilization_percent": qor.get("placement_utilization"),
            "total_power_mw": (
                float(qor["total_power_w"]) * 1000.0
                if qor.get("total_power_w") is not None
                else None
            ),
        },
        "next_action": (
            "No timing or routing fix is required for this target."
            if verdict == "PASS"
            else diagnosis.get("suggested_fix")
            or "Inspect negative slack or physical violations before sign-off."
        ),
    }


def _summary_markdown(
    module_name: str,
    overview: Dict[str, Any],
    artifacts: Dict[str, str],
) -> str:
    target = overview["target"]
    timing = overview["timing"]
    physical = overview["physical"]
    critical = timing.get("critical_path") or {}

    def value(item: Any, unit: str = "") -> str:
        if item is None:
            return "N/A"
        if isinstance(item, float):
            return f"{item:.3f}{unit}"
        return f"{item}{unit}"

    dominant_cells = ", ".join(
        f"{item['cell_type']} ({value(item['delay_ps'], ' ps')})"
        for item in critical.get("dominant_cell_types", [])[:5]
    ) or "N/A"

    return f"""# ChipAgent Result: {module_name}

## Verdict

**{overview['verdict']} — {overview['headline']}**

{overview['next_action']}

## What was tested

| Item | Value |
|---|---:|
| Target frequency | {value(target.get('frequency_mhz'), ' MHz')} |
| Clock period | {value(target.get('clock_period_ps'), ' ps')} |
| PVT corner | {value(target.get('corner'))} |
| Analysis | Post-route STA with extracted parasitics |

## Timing

| Check | Result | Meaning |
|---|---:|---|
| Setup slack | {value(timing.get('setup_slack_ps'), ' ps')} | PASS when >= 0 |
| Hold slack | {value(timing.get('hold_slack_ps'), ' ps')} | PASS when >= 0 |
| Setup TNS | {value(timing.get('setup_tns_ps'), ' ps')} | PASS when 0 |
| Hold TNS | {value(timing.get('hold_tns_ps'), ' ps')} | PASS when 0 |
| Core-clock Fmax | {value(timing.get('core_clock_fmax_mhz'), ' MHz')} | Tool-reported estimate |
| Setup violations | {value(timing.get('setup_violations'))} | PASS when 0 |
| Hold violations | {value(timing.get('hold_violations'))} | PASS when 0 |

## Critical path breakdown

| Item | Result |
|---|---:|
| Startpoint | {value(critical.get('startpoint'))} |
| Endpoint | {value(critical.get('endpoint'))} |
| Data-path delay | {value(critical.get('data_path_delay_ps'), ' ps')} |
| Cell delay | {value(critical.get('cell_delay_ps'), ' ps')} ({value(critical.get('cell_delay_percent'), '%')}) |
| Net delay | {value(critical.get('net_delay_ps'), ' ps')} ({value(critical.get('net_delay_percent'), '%')}) |
| Logic cells on path | {value(critical.get('cell_count'))} |
| Dominant cell types | {dominant_cells} |

## Physical checks

| Check | Result | Meaning |
|---|---:|---|
| Routing DRC errors | {value(physical.get('drc_errors'))} | PASS when 0 |
| Antenna violating nets | {value(physical.get('antenna_violating_nets'))} | PASS when 0 |
| Placed instance area | {value(physical.get('standard_cell_area_um2'), ' um^2')} | Includes hard macros; excludes fill cells |
| Placement utilization | {value(physical.get('placement_utilization_percent'), '%')} | Informational |
| Total power | {value(physical.get('total_power_mw'), ' mW')} | Corner/activity dependent |

## Detailed artifacts

- Final DEF: {artifacts.get('final_def', 'N/A')}
- Final GDS: {artifacts.get('final_gds', 'N/A')}
- Final ODB: {artifacts.get('final_odb', 'N/A')}
- Raw ORFS log: {artifacts.get('run.log', 'N/A')}

The JSON result keeps all raw QoR fields for debugging; this file is the recommended starting point.
"""


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

    if status == "partial" and _is_gds_export_failure(report):
        return {
            "status": "diagnosed",
            "stage": stage or "gds_export",
            "root_cause": "Post-route analysis completed, but the final GDS merge/export artifact was not produced.",
            "suggested_fix": "Check the KLayout merge log and image installation; timing, power, area, DEF, and ODB remain usable.",
            "evidence": evidence,
        }

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

    if "pdn-0233" in lower or "failed to generate full power grid" in lower:
        return {
            "status": "diagnosed",
            "stage": stage or "pdn",
            "root_cause": "ORFS could not build a complete power grid for one or more macro orientations.",
            "suggested_fix": "Check macro LEF VDD/VSS geometry and restrict unsupported SYMMETRY rotations before rerunning.",
            "evidence": evidence,
        }

    if "drt-0073" in lower or "no access point for" in lower:
        return {
            "status": "diagnosed",
            "stage": stage or "route",
            "root_cause": "A macro or standard-cell LEF pin has no legal routing access point.",
            "suggested_fix": "Inspect the failing cell's LEF pin and obstruction geometry; align pin rectangles to legal routing tracks before rerunning.",
            "evidence": evidence,
        }

    if "illegal instruction" in lower or "child killed" in lower:
        return {
            "status": "diagnosed",
            "stage": stage,
            "root_cause": (
                "The OpenROAD process crashed with an illegal instruction; "
                "this commonly occurs when an amd64 image runs through "
                "emulation on an ARM64 host."
            ),
            "suggested_fix": (
                "Use a native ARM64 OpenROAD image, or select "
                "timing_effort='closure_no_cts' to avoid the crashing CTS "
                "repair step while retaining later timing repairs."
            ),
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


def _is_gds_export_failure(report: str) -> bool:
    lower = report.lower()
    return (
        "cannot stat" in lower
        and ("6_1_merged.gds" in lower or "6_final.gds" in lower)
    ) or (
        "def2stream.py" in lower
        and "gds" in lower
        and "make:" in lower
        and "error" in lower
    )


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
