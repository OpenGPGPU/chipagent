"""MCP server: expose ChipAgent's capabilities to Claude Code (Phase 2 §3.2).

Claude Code natively speaks the Model Context Protocol. Running this module
starts a stdio MCP server whose tools are ChipAgent's DSE loop, closed-loop
generator, sim/align/elaborate checks, and task panel — so from inside a
Claude Code session the user can say "design a DMA, area < 300 cells, 250 MHz"
and Claude calls ``chipagent_run_dse`` here, gets the tradeoff table + selected
design back, and converses. No Claude Code modification required.

Wire it into Claude Code (project-local, picked up by anyone in the repo):

    # .mcp.json
    {
      "mcpServers": {
        "chipagent": {
          "command": "python",
          "args": ["-m", "chipagent.mcp"]
        }
      }
    }

or one-off::

    claude mcp add chipagent -- python -m chipagent.mcp

The server is a thin wrapper over the existing chipagent functions — it adds
no new domain logic, only the MCP tool contract. Tools run with the LLM *off*
inside chipagent (``use_llm=False``): Claude Code is the agent/brain here;
chipagent's job is the backend measurement + generation, not nested LLM calls.

Run standalone for a smoke test::

    python -m chipagent.mcp            # serves stdio (what Claude Code connects to)
    python -m chipagent.mcp --list     # print the tool catalogue
"""
from __future__ import annotations

import json
import os
import html
from pathlib import Path
from typing import Any, Dict, List, Optional

# Force the offline template path for tool execution: Claude Code is the
# reasoning layer; chipagent's internal LLM calls would just double-bill the
# gateway and add latency. Tools that need generation use the deterministic
# template fallback, which is exactly what we want behind an MCP tool.
os.environ.setdefault("CHIPAGENT_DISABLE_LLM", "1")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("chipagent")

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _to_text(obj: Any) -> str:
    """MCP tools return text content; serialise structured results as JSON."""
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _tool_result_text(res: Any, tool_name: str) -> str:
    """Serialize a ToolResult through the result trust-contract shim."""
    if hasattr(res, "normalized"):
        return _to_text(res.normalized(tool_name=tool_name))
    return _to_text(getattr(res, "result", res))


def _dse_summary(r: Dict[str, Any]) -> Dict[str, Any]:
    """Trim the DSE result to what an LLM agent finds useful (no full RTL
    blobs in the catalogue output — those are retrievable via dedicated tools)."""
    sel = r.get("selected", {}) or {}
    return {
        "status": r.get("status"),
        "iterations": r.get("iterations"),
        "spec": r.get("spec"),
        "partition": r.get("partition"),
        "selected": {
            "variant_id": sel.get("variant_id"),
            "variant": sel.get("variant"),
            "measurement": sel.get("measurement"),
            "score": sel.get("score"),
        },
        "selection_rationale": r.get("selection_rationale"),
        "partition_rationale": r.get("partition_rationale"),
        "pareto": [
            {"variant_id": c.get("variant_id"), "variant": c.get("variant"),
             "measurement": c.get("measurement"), "score": c.get("score")}
            for c in (r.get("pareto") or [])
        ],
        "tradeoff_table": r.get("tradeoff_table"),
        "artifacts": r.get("artifacts"),
    }


def _task_from_skill_inputs(name: str, inputs: Optional[Dict[str, Any]]) -> "TaskObject":
    """Build the TaskObject handed to the in-process text skill runner."""
    from .models import TaskObject

    data = inputs or {}
    return TaskObject(
        task_type=str(data.get("task_type") or name),
        module_name=data.get("module_name") or data.get("module") or "generated_module",
        description=data.get("description") or data.get("request") or "",
        interface=data.get("interface") or {},
        constraints=data.get("constraints") or {},
        output_requirements=data.get("output_requirements") or {},
    )


def _skill_filename(task: "TaskObject") -> str:
    suffixes = {
        "register_header": ".h",
        "hal_library": ".c",
        "linux_driver": ".c",
        "testbench_generation": "_tb.sv",
        "uvm_skeleton": "_uvm.sv",
    }
    suffix = suffixes.get(task.task_type, ".v")
    if suffix.startswith("_"):
        return f"{task.module_name}{suffix}"
    return f"{task.module_name}{suffix}"


def _persist_skill_output(output_dir: Optional[str], task: "TaskObject", code: str,
                          design_notes: str, checks: Dict[str, Any]) -> Dict[str, str]:
    if not output_dir:
        return {}
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    code_path = out / _skill_filename(task)
    report_path = out / f"{task.module_name}_{task.task_type}_report.json"
    code_path.write_text(code, encoding="utf-8")
    report_path.write_text(
        json.dumps({
            "task": task.to_dict(),
            "checks": checks,
            "design_notes": design_notes,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return {"code_path": str(code_path), "report_path": str(report_path)}


def _skill_post_checks(task: "TaskObject", code: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run cheap same-process validation that naturally pairs with a skill."""
    from .models import TaskObject
    from .tools import ToolContext
    from .tools.alignment import RegisterAlignmentTool
    from .tools.elaborate import ElaborateCheckTool
    from .tools.interface import SwHwInterfaceTool
    from .tools.simulation import RunSimulationTool

    checks: Dict[str, Any] = {}
    tool_task = TaskObject(
        task_type="hw_sw_codesign",
        module_name=task.module_name,
        description=task.description,
        interface=task.interface,
        constraints=task.constraints,
        output_requirements=task.output_requirements,
    )

    if task.task_type in {"rtl_generation", "reg_definition"}:
        res = ElaborateCheckTool().run(ToolContext(task=tool_task, inputs={"reg_code": code}))
        checks["elaborate"] = res.result
    if task.task_type == "register_header" and inputs.get("reg_code"):
        res = RegisterAlignmentTool().run(ToolContext(
            task=tool_task,
            inputs={"reg_code": inputs["reg_code"], "header_code": code},
        ))
        checks["alignment"] = res.result
    if task.task_type == "testbench_generation" and inputs.get("reg_code"):
        res = RunSimulationTool().run(ToolContext(
            task=tool_task,
            inputs={"reg_code": inputs["reg_code"], "tb_code": code},
        ))
        checks["simulation"] = res.result
    if task.task_type == "linux_driver" and inputs.get("reg_code"):
        res = SwHwInterfaceTool().run(ToolContext(
            task=tool_task,
            inputs={
                "reg_code": inputs["reg_code"],
                "driver_code": code,
                "module_name": task.module_name,
            },
        ))
        checks["interface"] = res.result
    return checks


def _directory_inventory(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"root": str(path), "exists": False, "files": []}
    files = []
    for fpath in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = fpath.relative_to(path).as_posix()
        files.append({"path": rel, "size": fpath.stat().st_size})
    return {"root": str(path), "exists": True, "files": files}


def _read_jsonish_file(path: Path, limit: int = 8000) -> Any:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        text = text[-limit:]
    if path.suffix == ".json":
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


# ======================================================================
# DSE / generation
# ======================================================================
@mcp.tool()
def chipagent_run_dse(
    request: str,
    area_budget: Optional[float] = None,
    fmax_target_mhz: Optional[float] = None,
    latency_budget_cycles: Optional[float] = None,
    power_budget_mw: Optional[float] = None,
    sw_cost_budget: Optional[float] = None,
    output_dir: Optional[str] = None,
) -> str:
    """Run the HW/SW co-design DSE loop. Given a feature request + non-functional
    targets, explores microarchitecture variants (pipeline/width/gating/interface),
    measures each (area/Fmax/latency/power/sw-cost), keeps a Pareto front, and
    returns the selected design + a tradeoff table + partition rationale.

    Pass only the targets you care about; unspecified targets are unconstrained.
    """
    from .dse import run_dse
    targets: Dict[str, Any] = {}
    if area_budget is not None:
        targets["area"] = area_budget
    if fmax_target_mhz is not None:
        targets["fmax"] = fmax_target_mhz
    if latency_budget_cycles is not None:
        targets["latency"] = latency_budget_cycles
    if power_budget_mw is not None:
        targets["power"] = power_budget_mw
    if sw_cost_budget is not None:
        targets["sw_cost"] = sw_cost_budget
    r = run_dse(request, targets=targets or None, output_dir=output_dir, use_llm=False)
    return _to_text(_dse_summary(r))


@mcp.tool()
def chipagent_run_sv_parameter_dse(
    reg_code: str,
    parameters: Dict[str, Any],
    module_name: str = "dut",
    tb_code: Optional[str] = None,
    output_dir: Optional[str] = None,
    run_physical: bool = False,
    max_candidates: int = 32,
) -> str:
    """Run parameter-grid DSE for an existing SystemVerilog/Verilog module.

    Each candidate wraps the supplied module with parameter overrides, runs the
    existing ChipAgent flow, and returns a ranked table. Physical ASAP7 flow is
    optional because it is much slower.
    """
    from .tools.base import ToolContext
    from .tools.sv_dse import SVParameterDSETool
    from .models import TaskObject

    res = SVParameterDSETool().run(ToolContext(
        task=TaskObject(task_type="sv_parameter_dse", module_name=module_name, description=""),
        inputs={
            "reg_code": reg_code or "",
            "tb_code": tb_code,
            "module_name": module_name,
            "parameters": parameters or {},
            "output_dir": output_dir,
            "run_physical": run_physical,
            "max_candidates": max_candidates,
        },
    ))
    return _tool_result_text(res, "run_sv_parameter_dse")


# ======================================================================
# Verification / measurement tools
# ======================================================================
@mcp.tool()
def chipagent_run_simulation(
    reg_code: str,
    tb_code: str,
    module_name: str = "dut",
    output_dir: Optional[str] = None,
    waveform_protocols: Optional[List[Dict[str, Any]]] = None,
    failure_time: Optional[int] = None,
) -> str:
    """Compile + run an RTL + testbench pair (iverilog/verilator when present)
    and report pass/fail + the VCD path."""
    from .tools import ToolContext, ToolLoader
    from .tools.simulation import RunSimulationTool
    from .models import TaskObject
    tool = RunSimulationTool()
    res = tool.run(ToolContext(
        task=TaskObject(task_type="hw_sw_codesign", module_name=module_name, description="",
                        constraints={"data_width": 32}),
        inputs={"reg_code": reg_code, "tb_code": tb_code, "output_dir": output_dir,
                "waveform_protocols": waveform_protocols,
                "failure_time": failure_time},
    ))
    return _tool_result_text(res, "run_simulation")


@mcp.tool()
def chipagent_analyze_waveform(
    waveform_path: str,
    protocols: List[Dict[str, Any]],
    module_name: str = "dut",
    failure_time: Optional[int] = None,
    window_before: int = 100,
    window_after: int = 20,
    output_dir: Optional[str] = None,
) -> str:
    """Analyze VCD/FST data and return evidence-backed ready/valid transaction
    timelines, stall timeouts, unknown values, and stalled-payload violations.

    Each protocol names ``valid`` and ``ready`` signals and may include
    ``clock``, ``id``, ``payload`` and ``max_stall_time``.
    """
    from .models import TaskObject
    from .tools import ToolContext
    from .tools.waveform_analysis import AnalyzeWaveformTool
    res = AnalyzeWaveformTool().run(ToolContext(
        task=TaskObject(task_type="waveform_debug", module_name=module_name,
                        description="analyze waveform data"),
        inputs={"waveform_path": waveform_path, "protocols": protocols,
                "failure_time": failure_time, "window_before": window_before,
                "window_after": window_after, "output_dir": output_dir},
    ))
    return _tool_result_text(res, "analyze_waveform")


@mcp.tool()
def chipagent_check_register_alignment(reg_code: str, header_code: str) -> str:
    """Check that the RTL register-block offsets match the C header defines."""
    from .tools import ToolContext
    from .tools.alignment import RegisterAlignmentTool
    from .models import TaskObject
    res = RegisterAlignmentTool().run(ToolContext(
        task=TaskObject(task_type="hw_sw_codesign", module_name="dut", description=""),
        inputs={"reg_code": reg_code, "header_code": header_code},
    ))
    return _tool_result_text(res, "check_register_alignment")


@mcp.tool()
def chipagent_elaborate(reg_code: str, module_name: str = "dut") -> str:
    """Lint-elaborate (verilator) + synthesis草估 (yosys) when available;
    returns cell count / lint issues. Degrades to skipped when no EDA on PATH."""
    from .tools import ToolContext
    from .tools.elaborate import ElaborateCheckTool
    from .models import TaskObject
    res = ElaborateCheckTool().run(ToolContext(
        task=TaskObject(task_type="hw_sw_codesign", module_name=module_name, description=""),
        inputs={"reg_code": reg_code},
    ))
    return _tool_result_text(res, "elaborate_check")


@mcp.tool()
def chipagent_check_sw_hw_interface(reg_code: str, driver_code: str, module_name: str = "dut") -> str:
    """Check the driver's interface contract against the RTL (include,
    compatible string, MMIO helpers, port references)."""
    from .tools import ToolContext
    from .tools.interface import SwHwInterfaceTool
    from .models import TaskObject
    res = SwHwInterfaceTool().run(ToolContext(
        task=TaskObject(task_type="hw_sw_codesign", module_name=module_name, description=""),
        inputs={"reg_code": reg_code, "driver_code": driver_code, "module_name": module_name},
    ))
    return _tool_result_text(res, "check_sw_hw_interface")


@mcp.tool()
def chipagent_analyze_coverage(
    sim_passed: Optional[str] = None,
    report_path: Optional[str] = None,
) -> str:
    """Analyze simulation coverage. When a real coverage report is available,
    parse toggle/functional coverage from it. Otherwise returns a deterministic
    stub derived from the simulation pass/fail outcome.

    Pass ``sim_passed`` ("passed"/"failed"/"skipped") for stub mode, or
    ``report_path`` to parse a real coverage report file.
    """
    from .tools import ToolContext
    from .tools.coverage import AnalyzeCoverageTool
    from .models import TaskObject
    res = AnalyzeCoverageTool().run(ToolContext(
        task=TaskObject(task_type="hw_sw_codesign", module_name="dut", description=""),
        inputs={"sim_result": {"passed": sim_passed or "skipped"},
                "report_path": report_path},
    ))
    return _tool_result_text(res, "analyze_coverage")


@mcp.tool()
def chipagent_run_sw_hw_cosim(
    reg_code: str,
    driver_code: str,
    test_scenario: Optional[str] = None,
) -> str:
    """Run HW/SW co-simulation (Phase 2 interface stub — real DPI backend not
    yet connected). Extracts register names from RTL and function signatures
    from the driver, validates cross-references, and returns the analysis.

    A future phase will swap in a real co-simulation engine without changing
    the MCP tool contract.
    """
    from .tools import ToolContext
    from .tools.cosim import SwHwCosimTool
    from .models import TaskObject
    res = SwHwCosimTool().run(ToolContext(
        task=TaskObject(task_type="hw_sw_codesign", module_name="dut", description=""),
        inputs={"reg_code": reg_code, "driver_code": driver_code,
                "test_scenario": test_scenario or ""},
    ))
    return _tool_result_text(res, "run_sw_hw_cosim")


@mcp.tool()
def chipagent_dry_run(
    request: str,
    output_dir: Optional[str] = None,
) -> str:
    """Run a dry-run preview of a closed-loop generation — shows what commands
    would be executed and what files would be produced, WITHOUT actually
    running anything. Useful for high-risk operations (synthesis, batch sim)
    where you want to inspect the plan before committing."""
    from .sandbox import Sandbox
    from .multistep import run_multistep

    # Run the orchestrator to get the plan, but intercept before persist.
    sandbox = Sandbox()
    # Preview: parse the request to see what would happen.
    from .parser import TaskParser
    from .llm import LLMClient
    from .config import Settings
    settings = Settings.load()
    parser = TaskParser(LLMClient(settings.llm), use_llm=False)
    task = parser.parse(request).to_dict()

    preview = {
        "dry_run": True,
        "request": request,
        "parsed_task": task,
        "would_produce": {
            "reg_code": f"{task.get('module_name', 'soc_block')}_reg_top.sv",
            "header_code": f"{task.get('module_name', 'soc_block')}_regs.h",
            "hal_code": f"{task.get('module_name', 'soc_block')}_hal.c",
            "tb_code": f"{task.get('module_name', 'soc_block')}_tb.sv",
            "driver_code": f"{task.get('module_name', 'soc_block')}_driver.c",
        },
        "output_dir": output_dir or "generated/",
        "commands_that_would_run": [
            "parse request -> task object",
            "reg_definition skill -> SV register block",
            "register_header skill -> C header file",
            "hal_library skill -> C HAL skeleton",
            "testbench_generation skill -> SV testbench",
            "linux_driver skill -> Linux driver skeleton",
            "run_simulation tool -> compile + run testbench",
            "check_register_alignment tool -> RTL vs header consistency",
            "persist_artifacts -> write files to output_dir",
        ],
    }
    return _to_text(preview)


# ======================================================================
# Catalogue + task panel
# ======================================================================
@mcp.tool()
def chipagent_list_skills() -> str:
    """List the text Skills chipagent can run (RTL/reg/header/hal/driver/tb/uvm)."""
    from .skills import SkillLoader
    skills = [
        {
            "name": s.name,
            "task_type": s.task_type,
            "description": s.description,
            "triggers": s.triggers,
            "resources": sorted(s.resources.keys()),
            "template": s.template_path,
        }
        for s in SkillLoader().discover()
    ]
    return _to_text(skills)


@mcp.tool()
def chipagent_run_skill(name: str, inputs: Optional[Dict[str, Any]] = None) -> str:
    """Run a named chipagent text skill in-process.

    ``name`` may be the skill name or task_type. ``inputs`` accepts:
    module_name, description/request, interface, constraints, output_requirements,
    context, output_dir, and optional paired artifacts such as reg_code for
    alignment/simulation/interface post-checks.
    """
    from .skills import SkillContext, SkillLoader, TextSkill

    data = inputs or {}
    loader = SkillLoader()
    loaded = loader.load(name)
    if loaded is None:
        loaded = next((s for s in loader.discover() if s.name == name), None)
    if loaded is None:
        return _to_text({
            "status": "failed",
            "error": f"unknown skill: {name}",
            "available": [s.task_type for s in loader.discover()],
        })

    task = _task_from_skill_inputs(loaded.task_type, data)
    skill = TextSkill(loaded, llm=None)
    result = skill.run(SkillContext(task=task, context=str(data.get("context") or "")))
    checks = dict(result.checks)
    checks.update(_skill_post_checks(task, result.code, data))
    artifacts = _persist_skill_output(data.get("output_dir"), task, result.code,
                                      result.design_notes, checks)
    return _to_text({
        "status": "completed" if result.code else "failed",
        "skill": loaded.name,
        "task": task.to_dict(),
        "code": result.code,
        "design_notes": result.design_notes,
        "checks": checks,
        "artifacts": artifacts,
    })


@mcp.tool()
def chipagent_list_tools() -> str:
    """List Python-backed tools with concise user-facing metadata."""
    from .tools import ToolLoader
    tools = []
    for tool in ToolLoader().discover():
        doc = (tool.__class__.__doc__ or "").strip().splitlines()
        tools.append({
            "name": tool.name,
            "category": _tool_category(tool.name),
            "description": _tool_description(tool.name, doc[0] if doc else ""),
            "heavy": tool.name in {"run_physical_flow_asap7"},
        })
    return _to_text(sorted(tools, key=lambda item: item["name"]))


def _tool_description(name: str, fallback: str = "") -> str:
    descriptions = {
        "run_simulation": "Compile and run RTL plus testbench with Icarus Verilog or Verilator.",
        "analyze_waveform": "Analyze VCD/FST protocol activity and emit structured failure evidence.",
        "run_synthesis": "Run Yosys synthesis and persist netlist/report artifacts.",
        "analyze_timing": "Analyze timing with OpenSTA when Liberty is supplied, otherwise return a Yosys structural estimate.",
        "optimize_area": "Run Yosys area-oriented optimization and report before/after structure.",
        "analyze_power": "Estimate power from synthesized structure and optional activity data.",
        "run_formality": "Run Yosys equivalence checking between reference and implementation RTL/netlist.",
        "run_physical_flow_asap7": "Run ASAP7 OpenROAD Flow Scripts from RTL to DEF/GDS and parse QoR.",
        "run_sv_parameter_dse": "Explore SystemVerilog parameter grids using the existing ChipAgent flow.",
        "create_floorplan": "Run or gate OpenROAD floorplanning when PDK inputs are available.",
        "run_placement": "Run or gate OpenROAD placement when PDK inputs are available.",
        "run_cts": "Run or gate OpenROAD clock-tree synthesis when PDK inputs are available.",
        "run_routing": "Run or gate OpenROAD routing when PDK inputs are available.",
        "run_drc_check": "Run Magic DRC checks when layout input is supplied.",
        "run_lvs_check": "Run Netgen LVS checks between layout and schematic netlists.",
    }
    return descriptions.get(name, fallback)


def _tool_category(name: str) -> str:
    if name.startswith("run_physical") or name in {"create_floorplan", "run_placement", "run_cts", "run_routing", "run_drc_check", "run_lvs_check"}:
        return "physical_design"
    if name in {"run_synthesis", "analyze_timing", "optimize_area", "analyze_power", "run_formality"}:
        return "synthesis"
    if name in {"run_simulation", "analyze_waveform", "elaborate_check", "analyze_coverage", "run_dpi_cosim"}:
        return "verification"
    if "interface" in name or "alignment" in name or "cosim" in name:
        return "hw_sw"
    return "utility"


# ======================================================================
# MCP resources
# ======================================================================
@mcp.resource(
    "file://artifacts/",
    name="chipagent_artifacts",
    description="Inventory of generated ChipAgent artifacts.",
    mime_type="application/json",
)
def chipagent_artifacts_resource() -> str:
    return _to_text(_directory_inventory(_REPO_ROOT / "generated"))


@mcp.resource(
    "file://register-definitions/",
    name="chipagent_register_definitions",
    description="Register-definition skill library references and examples.",
    mime_type="application/json",
)
def chipagent_register_definitions_resource() -> str:
    from .skills import SkillLoader

    skill = SkillLoader().load("reg_definition")
    if skill is None:
        return _to_text({"status": "missing"})
    return _to_text({
        "skill": skill.name,
        "task_type": skill.task_type,
        "description": skill.description,
        "resources": skill.resources,
    })


@mcp.resource(
    "file://audit-log/",
    name="chipagent_audit_log",
    description="ChipAgent execution and DSE audit logs.",
    mime_type="application/json",
)
def chipagent_audit_log_resource() -> str:
    log_dir = _REPO_ROOT / "logs"
    if not log_dir.exists():
        return _to_text({"root": str(log_dir), "exists": False, "logs": []})
    logs = []
    for fpath in sorted(p for p in log_dir.iterdir() if p.is_file()):
        if fpath.suffix not in {".json", ".jsonl", ".log", ".txt"}:
            continue
        logs.append({
            "path": fpath.name,
            "size": fpath.stat().st_size,
            "content": _read_jsonish_file(fpath),
        })
    return _to_text({"root": str(log_dir), "exists": True, "logs": logs})


@mcp.tool()
def chipagent_task_submit(request: str, require_approval: bool = False,
                          state_dir: Optional[str] = None,
                          output_dir: Optional[str] = None) -> str:
    """Submit a task via the task panel (records state + audit). When
    require_approval is set, an unapproved artifact is not persisted."""
    from .taskpanel import TaskPanel
    panel = TaskPanel(state_dir=state_dir)
    r = panel.submit(request, require_approval=require_approval, output_dir=output_dir)
    return _to_text({"task_id": r.get("task_id"), "status": r.get("status"),
                     "artifacts": r.get("artifacts"), "error": r.get("error")})


@mcp.tool()
def chipagent_task_approve(task_id: str, state_dir: Optional[str] = None) -> str:
    """Approve an awaiting-approval task so its artifact lands."""
    from .taskpanel import TaskPanel
    panel = TaskPanel(state_dir=state_dir)
    r = panel.approve(task_id)
    return _to_text({"task_id": r.get("task_id"), "status": r.get("status"),
                     "artifacts": r.get("artifacts")})


@mcp.tool()
def chipagent_task_rollback(task_id: str, state_dir: Optional[str] = None) -> str:
    """Roll back a task's persisted artifacts."""
    from .taskpanel import TaskPanel
    panel = TaskPanel(state_dir=state_dir)
    r = panel.rollback(task_id)
    return _to_text({"task_id": r.get("task_id"), "status": r.get("status"),
                     "removed": r.get("removed")})


@mcp.tool()
def chipagent_task_list(state_dir: Optional[str] = None) -> str:
    """List all submitted tasks (task_id, request, status, output_dir)."""
    from .taskpanel import TaskPanel
    panel = TaskPanel(state_dir=state_dir)
    return _to_text(panel.list())


@mcp.tool()
def chipagent_task_status(task_id: str, state_dir: Optional[str] = None) -> str:
    """Show detailed status of a submitted task."""
    from .taskpanel import TaskPanel
    panel = TaskPanel(state_dir=state_dir)
    r = panel.status(task_id)
    return _to_text(r)


@mcp.tool()
def chipagent_task_pause(task_id: str, state_dir: Optional[str] = None) -> str:
    """Pause a running task (records state + audit event)."""
    from .taskpanel import TaskPanel
    panel = TaskPanel(state_dir=state_dir)
    r = panel.pause(task_id)
    return _to_text(r)


@mcp.tool()
def chipagent_task_resume(task_id: str, state_dir: Optional[str] = None) -> str:
    """Resume a paused task."""
    from .taskpanel import TaskPanel
    panel = TaskPanel(state_dir=state_dir)
    r = panel.resume(task_id)
    return _to_text(r)



# =============================================================================
# Phase 3: Synthesis Tools (5 tools)
# =============================================================================
@mcp.tool()
def chipagent_run_synthesis(
    reg_code: str,
    constraints: Optional[str] = None,
    module_name: str = "dut",
    output_dir: Optional[str] = None,
) -> str:
    """Run full synthesis flow using Yosys.

    Performs RTL synthesis: read_verilog → synth → stat → write_verilog.
    Returns an explicit error when Yosys is unavailable.

    Args:
        reg_code: Verilog RTL code to synthesize
        constraints: Optional SDC-like constraints (as string)
        module_name: Top module name (default: "dut")
        output_dir: Optional directory for netlist/report/script artifacts

    Returns:
        Synthesis report with cell count, area, and netlist
    """
    from .tools.base import ToolContext
    from .tools.synth_run import SynthesisTool
    from .models import TaskObject
    res = SynthesisTool().run(ToolContext(
        task=TaskObject(task_type="synthesis", module_name=module_name, description=""),
        inputs={"reg_code": reg_code, "constraints": constraints or "", "output_dir": output_dir},
    ))
    return _tool_result_text(res, "run_synthesis")


@mcp.tool()
def chipagent_analyze_timing(
    netlist: Optional[str] = None,
    reg_code: Optional[str] = None,
    module_name: str = "dut",
    output_dir: Optional[str] = None,
    liberty: Optional[str] = None,
    liberty_file: Optional[str] = None,
    sdc: Optional[str] = None,
) -> str:
    """Perform static timing analysis on synthesized netlist.

    Uses OpenSTA if available, falls back to yosys stat or estimation.

    Args:
        netlist: Synthesized netlist (Verilog)
        reg_code: Original RTL code (alternative to netlist)
        module_name: Top module name (default: "dut")
        output_dir: Optional directory for timing reports/scripts
        liberty: Liberty timing library content for OpenSTA
        liberty_file: Path to a Liberty timing library for OpenSTA
        sdc: Optional SDC constraints content

    Returns:
        Timing report with slack, WNS, TNS, and critical path
    """
    from .tools.base import ToolContext
    from .tools.synth_timing import TimingAnalysisTool
    from .models import TaskObject
    res = TimingAnalysisTool().run(ToolContext(
        task=TaskObject(task_type="timing_analysis", module_name=module_name, description=""),
        inputs={
            "netlist": netlist or "",
            "reg_code": reg_code or "",
            "output_dir": output_dir,
            "liberty": liberty or "",
            "liberty_file": liberty_file or "",
            "sdc": sdc or "",
        },
    ))
    return _tool_result_text(res, "analyze_timing")


@mcp.tool()
def chipagent_optimize_area(
    netlist: Optional[str] = None,
    reg_code: Optional[str] = None,
    area_constraint: Optional[float] = None,
    module_name: str = "dut",
    output_dir: Optional[str] = None,
) -> str:
    """Optimize design for area using Yosys.

    Applies area optimization strategies: opt_clean, opt_merge, opt_expr.

    Args:
        netlist: Synthesized netlist (Verilog)
        reg_code: Original RTL code (alternative to netlist)
        area_constraint: Target area constraint (optional)
        module_name: Top module name (default: "dut")
        output_dir: Optional directory for optimized netlist/report/script artifacts

    Returns:
        Optimization report with area reduction percentage
    """
    from .tools.base import ToolContext
    from .tools.synth_area import AreaOptimizeTool
    from .models import TaskObject
    res = AreaOptimizeTool().run(ToolContext(
        task=TaskObject(task_type="area_optimization", module_name=module_name, description=""),
        inputs={
            "netlist": netlist or "",
            "reg_code": reg_code or "",
            "area_constraint": area_constraint,
            "output_dir": output_dir,
        },
    ))
    return _tool_result_text(res, "optimize_area")


@mcp.tool()
def chipagent_analyze_power(
    netlist: Optional[str] = None,
    reg_code: Optional[str] = None,
    vcd_path: Optional[str] = None,
    module_name: str = "dut",
    output_dir: Optional[str] = None,
) -> str:
    """Analyze power consumption using Yosys + VCD.

    Estimates dynamic and leakage power from netlist and optional VCD.

    Args:
        netlist: Synthesized netlist (Verilog)
        reg_code: Original RTL code (alternative to netlist)
        vcd_path: Path to VCD file for switching activity (optional)
        module_name: Top module name (default: "dut")
        output_dir: Optional directory for power reports/scripts

    Returns:
        Power analysis report with dynamic, leakage, and total power
    """
    from .tools.base import ToolContext
    from .tools.synth_power import PowerAnalysisTool
    from .models import TaskObject
    res = PowerAnalysisTool().run(ToolContext(
        task=TaskObject(task_type="power_analysis", module_name=module_name, description=""),
        inputs={
            "netlist": netlist or "",
            "reg_code": reg_code or "",
            "vcd_path": vcd_path,
            "output_dir": output_dir,
        },
    ))
    return _tool_result_text(res, "analyze_power")


@mcp.tool()
def chipagent_run_formality(
    reference_netlist: str,
    implementation_netlist: str,
    module_name: str = "dut",
    output_dir: Optional[str] = None,
) -> str:
    """Perform formal verification using Yosys equiv_check.

    Compares reference and implementation netlists for equivalence.

    Args:
        reference_netlist: Reference (golden) netlist
        implementation_netlist: Implementation netlist to verify
        module_name: Top module name (default: "dut")
        output_dir: Optional directory for equivalence reports/scripts/netlists

    Returns:
        Formal verification report with equivalence status and mismatches
    """
    from .tools.base import ToolContext
    from .tools.synth_formality import FormalVerifyTool
    from .models import TaskObject
    res = FormalVerifyTool().run(ToolContext(
        task=TaskObject(task_type="formal_verification", module_name=module_name, description=""),
        inputs={
            "reference_netlist": reference_netlist,
            "implementation_netlist": implementation_netlist,
            "output_dir": output_dir,
        },
    ))
    return _tool_result_text(res, "run_formality")


# =============================================================================
# Phase 3: Physical Design Tools (6 tools)
# =============================================================================
@mcp.tool()
def chipagent_create_floorplan(
    netlist: Optional[str] = None,
    reg_code: Optional[str] = None,
    constraints: Optional[Dict[str, Any]] = None,
    tech_lef: Optional[str] = None,
    liberty: Optional[str] = None,
    liberty_file: Optional[str] = None,
    module_name: str = "dut"
) -> str:
    """Create floorplan using OpenROAD.

    Initializes floorplan with die area, core area, and placement constraints.

    Args:
        netlist: Synthesized netlist (Verilog)
        reg_code: Original RTL code (alternative to netlist)
        constraints: Dict with utilization, aspect_ratio, etc.
        tech_lef: Technology LEF content or path required by OpenROAD
        liberty: Liberty timing library content required by OpenROAD
        liberty_file: Path to a Liberty timing library required by OpenROAD
        module_name: Top module name (default: "dut")

    Returns:
        Floorplan report with die area, core area, and utilization
    """
    from .tools.base import ToolContext
    from .tools.phys_floorplan import FloorplanTool
    from .models import TaskObject
    res = FloorplanTool().run(ToolContext(
        task=TaskObject(task_type="floorplan", module_name=module_name, description=""),
        inputs={
            "netlist": netlist or "",
            "reg_code": reg_code or "",
            "constraints": constraints or {},
            "tech_lef": tech_lef or "",
            "liberty": liberty or "",
            "liberty_file": liberty_file or "",
        },
    ))
    return _tool_result_text(res, "create_floorplan")


@mcp.tool()
def chipagent_run_placement(
    netlist: Optional[str] = None,
    reg_code: Optional[str] = None,
    floorplan: Optional[Dict[str, Any]] = None,
    tech_lef: Optional[str] = None,
    liberty: Optional[str] = None,
    liberty_file: Optional[str] = None,
    module_name: str = "dut"
) -> str:
    """Run placement using OpenROAD.

    Performs global and detailed placement of standard cells.

    Args:
        netlist: Synthesized netlist (Verilog)
        reg_code: Original RTL code (alternative to netlist)
        floorplan: Floorplan data from create_floorplan
        tech_lef: Technology LEF content or path required by OpenROAD
        liberty: Liberty timing library content required by OpenROAD
        liberty_file: Path to a Liberty timing library required by OpenROAD
        module_name: Top module name (default: "dut")

    Returns:
        Placement report with cell count, timing slack, and congestion
    """
    from .tools.base import ToolContext
    from .tools.phys_placement import PlacementTool
    from .models import TaskObject
    res = PlacementTool().run(ToolContext(
        task=TaskObject(task_type="placement", module_name=module_name, description=""),
        inputs={
            "netlist": netlist or "",
            "reg_code": reg_code or "",
            "floorplan": floorplan or {},
            "tech_lef": tech_lef or "",
            "liberty": liberty or "",
            "liberty_file": liberty_file or "",
        },
    ))
    return _tool_result_text(res, "run_placement")


@mcp.tool()
def chipagent_run_cts(
    netlist: Optional[str] = None,
    reg_code: Optional[str] = None,
    placement: Optional[Dict[str, Any]] = None,
    tech_lef: Optional[str] = None,
    liberty: Optional[str] = None,
    liberty_file: Optional[str] = None,
    module_name: str = "dut"
) -> str:
    """Run clock tree synthesis using OpenROAD.

    Synthesizes clock tree with target skew and insertion delay.

    Args:
        netlist: Synthesized netlist (Verilog)
        reg_code: Original RTL code (alternative to netlist)
        placement: Placement data from run_placement
        tech_lef: Technology LEF content or path required by OpenROAD
        liberty: Liberty timing library content required by OpenROAD
        liberty_file: Path to a Liberty timing library required by OpenROAD
        module_name: Top module name (default: "dut")

    Returns:
        CTS report with skew, insertion delay, and buffer count
    """
    from .tools.base import ToolContext
    from .tools.phys_cts import CTSTool
    from .models import TaskObject
    res = CTSTool().run(ToolContext(
        task=TaskObject(task_type="cts", module_name=module_name, description=""),
        inputs={
            "netlist": netlist or "",
            "reg_code": reg_code or "",
            "placement": placement or {},
            "tech_lef": tech_lef or "",
            "liberty": liberty or "",
            "liberty_file": liberty_file or "",
        },
    ))
    return _tool_result_text(res, "run_cts")


@mcp.tool()
def chipagent_run_routing(
    netlist: Optional[str] = None,
    reg_code: Optional[str] = None,
    cts: Optional[Dict[str, Any]] = None,
    tech_lef: Optional[str] = None,
    liberty: Optional[str] = None,
    liberty_file: Optional[str] = None,
    module_name: str = "dut"
) -> str:
    """Run routing using OpenROAD.

    Performs global and detailed routing of signal nets.

    Args:
        netlist: Synthesized netlist (Verilog)
        reg_code: Original RTL code (alternative to netlist)
        cts: CTS data from run_cts
        tech_lef: Technology LEF content or path required by OpenROAD
        liberty: Liberty timing library content required by OpenROAD
        liberty_file: Path to a Liberty timing library required by OpenROAD
        module_name: Top module name (default: "dut")

    Returns:
        Routing report with wirelength, DRC violations, and congestion
    """
    from .tools.base import ToolContext
    from .tools.phys_routing import RoutingTool
    from .models import TaskObject
    res = RoutingTool().run(ToolContext(
        task=TaskObject(task_type="routing", module_name=module_name, description=""),
        inputs={
            "netlist": netlist or "",
            "reg_code": reg_code or "",
            "cts": cts or {},
            "tech_lef": tech_lef or "",
            "liberty": liberty or "",
            "liberty_file": liberty_file or "",
        },
    ))
    return _tool_result_text(res, "run_routing")


@mcp.tool()
def chipagent_run_physical_flow_asap7(
    reg_code: str,
    rtl_files: Optional[List[str]] = None,
    module_name: str = "dut",
    clock_port: str = "clk",
    clock_period: float = 310.0,
    core_utilization: int = 10,
    place_density: float = 0.20,
    corner: str = "WC",
    cell_vt: str = "RVT",
    output_dir: Optional[str] = None,
    timeout: int = 1800,
    cache: bool = True,
    clean: bool = False,
    macro_lefs: Optional[List[str]] = None,
    macro_libs: Optional[List[str]] = None,
    macro_gds: Optional[List[str]] = None,
    macro_placement_tcl: Optional[str] = None,
    timing_effort: str = "explore",
    synthesis_engine: str = "syn",
    sv_frontend: str = "native",
    enable_retiming: bool = False,
    swap_arithmetic_operators: bool = False,
    max_fanout: Optional[int] = None,
    high_fanout_nets: Optional[List[str]] = None,
    high_fanout_max: int = 8,
    setup_slack_margin: float = 0.0,
    abc_clock_period_ps: Optional[float] = None,
    io_delay_percent: float = 0.2,
    io_false_path_ports: Optional[List[str]] = None,
    post_floorplan_tcl: Optional[str] = None,
) -> str:
    """Run the ASAP7 OpenROAD Flow Scripts physical implementation flow.

    This is the preferred physical-design path for ChipAgent. It packages the
    supplied RTL into an ORFS-compatible ASAP7 design and invokes make inside
    the configured OpenROAD Docker image.

    Args:
        reg_code: RTL code to implement; may be empty when rtl_files is provided
        rtl_files: Ordered Verilog/SystemVerilog source paths for multi-file designs
        module_name: Top module name
        clock_port: Clock port name used in the generated SDC
        clock_period: ASAP7 SDC clock period, default 310
        core_utilization: ORFS core utilization percentage
        place_density: ORFS placement density
        corner: ASAP7 analysis corner: WC (SS), TC (TT), or BC (FF)
        cell_vt: ASAP7 threshold-voltage library: RVT, LVT, or SLVT
        output_dir: Directory for ORFS work tree, logs, reports, and results.
            When omitted, ChipAgent creates a sequential directory such as
            generated/physical/001_dut.
        timeout: Flow timeout in seconds
        cache: Reuse an existing matching physical result when possible
        clean: Remove previous ORFS work tree before running
        macro_lefs: Macro abstract physical views to add to ORFS
        macro_libs: Matching macro Liberty timing/power views
        macro_gds: Matching macro GDS views for final layout merge
        macro_placement_tcl: Optional fixed macro-placement Tcl script
        timing_effort: ORFS optimization profile: explore, closure, or closure_no_cts
        synthesis_engine: ORFS synthesis engine: syn or yosys
        sv_frontend: SystemVerilog frontend: native or sv2v
        enable_retiming: Apply ORFS/Yosys ABC sequential retiming to the top module
        swap_arithmetic_operators: Generate multiple adder/multiplier architectures
            and let OpenROAD select implementations using physical timing
        max_fanout: Optional SDC maximum fanout constraint
        high_fanout_nets: Hierarchical net patterns whose fanout should be
            repaired without globally over-buffering the design
        high_fanout_max: Maximum fanout applied to the named high-fanout nets
        setup_slack_margin: ORFS setup repair margin in nanoseconds
        abc_clock_period_ps: Optional tighter delay target passed to Yosys/ABC

    Returns:
        ORFS flow status with collected DEF/GDS/report artifacts when present
    """
    from .tools.base import ToolContext
    from .tools.phys_flow_asap7 import ASAP7PhysicalFlowTool
    from .models import TaskObject

    res = ASAP7PhysicalFlowTool().run(ToolContext(
        task=TaskObject(task_type="physical_flow_asap7", module_name=module_name, description=""),
        inputs={
            "reg_code": reg_code or "",
            "rtl_files": rtl_files,
            "module_name": module_name,
            "clock_port": clock_port,
            "clock_period": clock_period,
            "core_utilization": core_utilization,
            "place_density": place_density,
            "corner": corner,
            "cell_vt": cell_vt,
            "output_dir": output_dir,
            "timeout": timeout,
            "cache": cache,
            "clean": clean,
            "macro_lefs": macro_lefs,
            "macro_libs": macro_libs,
            "macro_gds": macro_gds,
            "macro_placement_tcl": macro_placement_tcl,
            "timing_effort": timing_effort,
            "synthesis_engine": synthesis_engine,
            "sv_frontend": sv_frontend,
            "enable_retiming": enable_retiming,
            "swap_arithmetic_operators": swap_arithmetic_operators,
            "max_fanout": max_fanout,
            "high_fanout_nets": high_fanout_nets,
            "high_fanout_max": high_fanout_max,
            "setup_slack_margin": setup_slack_margin,
            "abc_clock_period_ps": abc_clock_period_ps,
            "io_delay_percent": io_delay_percent,
            "io_false_path_ports": io_false_path_ports,
            "post_floorplan_tcl": post_floorplan_tcl,
        },
    ))
    return _tool_result_text(res, "run_physical_flow_asap7")


@mcp.tool()
def chipagent_run_physical_flow(
    reg_code: str,
    rtl_files: Optional[List[str]] = None,
    module_name: str = "dut",
    platform: str = "asap7",
    clock_port: str = "clk",
    clock_period: float = 310.0,
    core_utilization: int = 10,
    place_density: float = 0.20,
    output_dir: Optional[str] = None,
    timeout: int = 1800,
    cache: bool = True,
    clean: bool = False,
    timing_effort: str = "explore",
    synthesis_engine: str = "syn",
    sv_frontend: str = "native",
    enable_retiming: bool = False,
    swap_arithmetic_operators: bool = False,
    max_fanout: Optional[int] = None,
    high_fanout_nets: Optional[List[str]] = None,
    high_fanout_max: int = 8,
    setup_slack_margin: float = 0.0,
    abc_clock_period_ps: Optional[float] = None,
    platform_options: Optional[Dict[str, Any]] = None,
) -> str:
    """Run a physical implementation flow without naming a process in the API.

    The platform is an explicit argument instead of being baked into the tool
    name.  Today only ``asap7`` is wired to an OpenROAD/ORFS backend; other
    platforms return an honest ``unavailable`` result.  Technology-specific
    settings such as ``corner``, ``cell_vt``, and macro collateral belong in
    ``platform_options``.

    Args:
        reg_code: RTL code to implement; may be empty when rtl_files is provided
        rtl_files: Ordered Verilog/SystemVerilog source paths for multi-file designs
        module_name: Top module name
        platform: Physical-design platform, currently ``asap7``
        clock_port: Clock port name used in the generated SDC
        clock_period: SDC clock period
        core_utilization: Core utilization percentage
        place_density: Placement density
        output_dir: Directory for flow work tree, logs, reports, and results
        timeout: Flow timeout in seconds
        cache: Reuse an existing matching physical result when possible
        clean: Remove previous flow work tree before running
        timing_effort: Optimization profile supported by the backend
        synthesis_engine: Synthesis engine supported by the backend
        sv_frontend: SystemVerilog frontend supported by the backend
        enable_retiming: Apply sequential retiming
        swap_arithmetic_operators: Generate multiple arithmetic architectures
        max_fanout: Optional global maximum fanout constraint
        high_fanout_nets: Hierarchical net patterns for targeted fanout repair
        high_fanout_max: Maximum fanout for the named high-fanout nets
        setup_slack_margin: Setup repair margin
        abc_clock_period_ps: Optional tighter synthesis delay target
        platform_options: Backend-specific options (e.g. ASAP7 corner,
            cell_vt, macro LEF/lib/GDS, and macro placement Tcl)

    Returns:
        Physical flow status with collected DEF/GDS/report artifacts
    """
    if platform.lower() != "asap7":
        return _to_text({
            "status": "unavailable",
            "platform": platform,
            "tool_available": False,
            "message": (
                f"platform '{platform}' is not wired to a physical-design "
                "backend yet; supported platforms: asap7"
            ),
        })
    opts = dict(platform_options or {})
    return chipagent_run_physical_flow_asap7(
        reg_code=reg_code,
        rtl_files=rtl_files,
        module_name=module_name,
        clock_port=clock_port,
        clock_period=clock_period,
        core_utilization=core_utilization,
        place_density=place_density,
        corner=opts.get("corner", "WC"),
        cell_vt=opts.get("cell_vt", "RVT"),
        output_dir=output_dir,
        timeout=timeout,
        cache=cache,
        clean=clean,
        macro_lefs=opts.get("macro_lefs"),
        macro_libs=opts.get("macro_libs"),
        macro_gds=opts.get("macro_gds"),
        macro_placement_tcl=opts.get("macro_placement_tcl"),
        timing_effort=timing_effort,
        synthesis_engine=synthesis_engine,
        sv_frontend=sv_frontend,
        enable_retiming=enable_retiming,
        swap_arithmetic_operators=swap_arithmetic_operators,
        max_fanout=max_fanout,
        high_fanout_nets=high_fanout_nets,
        high_fanout_max=high_fanout_max,
        setup_slack_margin=setup_slack_margin,
        abc_clock_period_ps=abc_clock_period_ps,
        io_delay_percent=opts.get("io_delay_percent", 0.2),
    )


@mcp.tool()
def chipagent_run_synthesis_asap7(
    reg_code: str,
    rtl_files: Optional[List[str]] = None,
    module_name: str = "dut",
    clock_port: str = "clk",
    clock_period: float = 1000.0,
    corner: str = "TC",
    cell_vt: str = "SLVT",
    output_dir: Optional[str] = None,
    timeout: int = 3600,
    cache: bool = True,
    clean: bool = False,
    macro_lefs: Optional[List[str]] = None,
    macro_libs: Optional[List[str]] = None,
    synthesis_engine: str = "yosys",
    sv_frontend: str = "native",
    enable_retiming: bool = True,
    abc_clock_period_ps: Optional[float] = None,
) -> str:
    """Run ORFS ASAP7 synthesis only and report pre-layout setup Fmax.

    This is the fast 1 GHz gate: it performs Yosys/ABC synthesis against the
    selected ASAP7 corner/VT library and then runs STA on the synthesized ODB.
    No floorplan, placement, CTS, or routing is executed.
    """
    from .tools.base import ToolContext
    from .tools.phys_flow_asap7 import ASAP7PhysicalFlowTool
    from .models import TaskObject

    res = ASAP7PhysicalFlowTool().run(ToolContext(
        task=TaskObject(
            task_type="physical_flow_asap7",
            module_name=module_name,
            description="",
        ),
        inputs={
            "reg_code": reg_code or "",
            "rtl_files": rtl_files,
            "module_name": module_name,
            "clock_port": clock_port,
            "clock_period": clock_period,
            "corner": corner,
            "cell_vt": cell_vt,
            "output_dir": output_dir,
            "timeout": timeout,
            "cache": cache,
            "clean": clean,
            "macro_lefs": macro_lefs,
            "macro_libs": macro_libs,
            "synthesis_engine": synthesis_engine,
            "sv_frontend": sv_frontend,
            "enable_retiming": enable_retiming,
            "abc_clock_period_ps": abc_clock_period_ps,
            "synthesis_only": True,
        },
    ))
    return _tool_result_text(res, "run_synthesis_asap7")


@mcp.tool()
def chipagent_run_drc_check(
    layout: Optional[str] = None,
    netlist: Optional[str] = None,
    module_name: str = "dut",
    output_dir: Optional[str] = None,
) -> str:
    """Run DRC check using Magic.

    Performs design rule checks on layout to ensure manufacturability.

    Args:
        layout: Layout data (GDS/DEF format)
        netlist: Netlist (alternative to layout)
        module_name: Top module name (default: "dut")

    Returns:
        DRC report with violations count and categories
    """
    from .tools.base import ToolContext
    from .tools.phys_drc import DRCCheckTool
    from .models import TaskObject
    res = DRCCheckTool().run(ToolContext(
        task=TaskObject(task_type="drc", module_name=module_name, description=""),
        inputs={
            "layout": layout or "",
            "netlist": netlist or "",
            "output_dir": output_dir,
        },
    ))
    return _tool_result_text(res, "run_drc_check")


@mcp.tool()
def chipagent_run_lvs_check(
    layout: str,
    netlist: str,
    module_name: str = "dut",
    output_dir: Optional[str] = None,
) -> str:
    """Run LVS check using Netgen.

    Compares layout extracted netlist with schematic netlist for consistency.

    Args:
        layout: Layout netlist (SPICE format)
        netlist: Schematic netlist (SPICE/Verilog format)
        module_name: Top module name (default: "dut")

    Returns:
        LVS report with match status and mismatches
    """
    from .tools.base import ToolContext
    from .tools.phys_lvs import LVSCheckTool
    from .models import TaskObject
    res = LVSCheckTool().run(ToolContext(
        task=TaskObject(task_type="lvs", module_name=module_name, description=""),
        inputs={
            "layout": layout,
            "netlist": netlist,
            "output_dir": output_dir,
        },
    ))
    return _tool_result_text(res, "run_lvs_check")


# =============================================================================
# Phase 3: Knowledge Tools (7 tools)
# =============================================================================
@mcp.tool()
def chipagent_query_knowledge_base(
    query: str,
    top_k: int = 5
) -> str:
    """Query the knowledge base for relevant information.

    Searches chipagent knowledge sources (skill references, examples, documentation)
    using TF-IDF similarity and keyword matching.

    Args:
        query: Natural language query
        top_k: Number of top results to return (default: 5)

    Returns:
        List of relevant knowledge chunks with scores and sources
    """
    from .knowledge import KnowledgeBase
    kb = KnowledgeBase()
    results = kb.query(query, top_k=top_k)
    return _to_text({
        "status": "success",
        "query": query,
        "results": results,
        "count": len(results),
    })


@mcp.tool()
def chipagent_search_code_examples(
    description: str,
    top_k: int = 3
) -> str:
    """Search for code examples matching description.

    Finds relevant code examples from skill examples/ directories.

    Args:
        description: Description of what you're looking for
        top_k: Number of examples to return (default: 3)

    Returns:
        List of matching code examples with source and score
    """
    from .knowledge import KnowledgeBase
    kb = KnowledgeBase()
    results = kb.search_examples(description, top_k=top_k)
    return _to_text({
        "status": "success",
        "description": description,
        "examples": results,
        "count": len(results),
    })


@mcp.tool()
def chipagent_consult_architecture(
    requirement: str,
    top_k: int = 5
) -> str:
    """Get architecture consultation based on requirements.

    Searches for architectural patterns and best practices.

    Args:
        requirement: Design requirement description
        top_k: Number of relevant chunks to consider (default: 5)

    Returns:
        Architecture consultation with relevant patterns and examples
    """
    from .knowledge import KnowledgeBase
    kb = KnowledgeBase()
    results = kb.query(requirement, top_k=top_k)

    # Extract architecture-related content
    architecture_info = [
        r for r in results
        if any(kw in r.get("content", "").lower()
               for kw in ["architecture", "design", "pattern", "best practice"])
    ]

    return _to_text({
        "status": "success",
        "requirement": requirement,
        "consultation": architecture_info or results,
        "count": len(architecture_info or results),
    })


@mcp.tool()
def chipagent_diagnose_issue(
    error_message: str,
    top_k: int = 5
) -> str:
    """Diagnose an issue based on error message.

    Searches for similar issues and solutions in knowledge base.

    Args:
        error_message: Error message or issue description
        top_k: Number of relevant chunks to consider (default: 5)

    Returns:
        Diagnosis with potential solutions and related issues
    """
    from .knowledge import KnowledgeBase
    kb = KnowledgeBase()
    results = kb.query(error_message, top_k=top_k)

    # Extract diagnostic information
    diagnosis = {
        "error": error_message,
        "potential_causes": [],
        "suggested_solutions": [],
        "related_issues": results,
    }

    # Simple heuristic: look for error/solution patterns
    for r in results:
        content = r.get("content", "")
        if "error" in content.lower() or "fail" in content.lower():
            diagnosis["potential_causes"].append(content[:200])
        if "solution" in content.lower() or "fix" in content.lower():
            diagnosis["suggested_solutions"].append(content[:200])

    return _to_text({
        "status": "success",
        "diagnosis": diagnosis,
    })


@mcp.tool()
def chipagent_generate_documentation(
    module_name: str,
    design_info: Optional[str] = None,
    top_k: int = 5
) -> str:
    """Generate documentation for a module.

    Creates documentation based on design information and knowledge base.

    Args:
        module_name: Name of the module to document
        design_info: Optional design information to include
        top_k: Number of relevant chunks to consider (default: 5)

    Returns:
        Generated documentation in markdown format
    """
    from .knowledge import KnowledgeBase
    kb = KnowledgeBase()

    # Search for similar modules
    results = kb.query(f"{module_name} documentation", top_k=top_k)

    # Generate documentation structure
    doc = {
        "module_name": module_name,
        "title": f"{module_name} Module Documentation",
        "sections": {
            "overview": f"Documentation for {module_name} module.",
            "design_info": design_info or "No design information provided.",
            "related_examples": [r.get("source") for r in results[:3]],
            "references": [r.get("content", "")[:200] for r in results],
        },
    }

    return _to_text({
        "status": "success",
        "documentation": doc,
    })


@mcp.tool()
def chipagent_search_software_reference(
    query: str,
    top_k: int = 5
) -> str:
    """Search for software reference documentation.

    Searches for API documentation, driver guides, and software references.

    Args:
        query: Search query (API name, driver function, etc.)
        top_k: Number of results to return (default: 5)

    Returns:
        List of relevant software reference documents
    """
    from .knowledge import KnowledgeBase
    kb = KnowledgeBase()
    results = kb.query(query, top_k=top_k)

    # Filter for software-related content
    software_refs = [
        r for r in results
        if any(kw in r.get("source", "").lower()
               for kw in ["driver", "hal", "software", "api", "register"])
    ]

    return _to_text({
        "status": "success",
        "query": query,
        "references": software_refs or results,
        "count": len(software_refs or results),
    })


@mcp.tool()
def chipagent_consult_sw_hw_co_design(
    requirement: str,
    top_k: int = 5
) -> str:
    """Get SW/HW co-design consultation.

    Searches for co-design patterns, interface specifications, and integration guides.

    Args:
        requirement: Co-design requirement description
        top_k: Number of relevant chunks to consider (default: 5)

    Returns:
        Co-design consultation with patterns and best practices
    """
    from .knowledge import KnowledgeBase
    kb = KnowledgeBase()
    results = kb.query(requirement, top_k=top_k)

    # Extract co-design related content
    codesign_info = [
        r for r in results
        if any(kw in r.get("content", "").lower()
               for kw in ["co-design", "interface", "integration", "sw/hw", "hardware-software"])
    ]

    return _to_text({
        "status": "success",
        "requirement": requirement,
        "consultation": codesign_info or results,
        "count": len(codesign_info or results),
    })


# =============================================================================
# Phase 3: DPI Co-simulation (1 tool)
# =============================================================================
@mcp.tool()
def chipagent_run_dpi_cosim(
    reg_code: str,
    dpi_code: Optional[str] = None,
    testbench: str = "",
    simulation_cycles: int = 100,
    module_name: str = "dut"
) -> str:
    """Run DPI co-simulation using Verilator.

    Performs co-simulation between C/C++ and Verilog using Verilator DPI.

    Args:
        reg_code: Verilog RTL code with DPI imports
        dpi_code: C/C++ DPI implementation code (optional)
        testbench: Verilog testbench code
        simulation_cycles: Number of cycles to simulate (default: 100)
        module_name: Top module name (default: "dut")

    Returns:
        Co-simulation report with compilation status, simulation results,
        and DPI interface analysis
    """
    from .tools.base import ToolContext
    from .tools.dpi_cosim import DPICosimTool
    from .models import TaskObject
    res = DPICosimTool().run(ToolContext(
        task=TaskObject(task_type="dpi_cosim", module_name=module_name, description=""),
        inputs={
            "reg_code": reg_code,
            "dpi_code": dpi_code or "",
            "testbench": testbench,
            "simulation_cycles": simulation_cycles,
        },
    ))
    return _tool_result_text(res, "run_dpi_cosim")


# =============================================================================
# Phase 3: Multi-Agent Coordination (2 tools)
# =============================================================================
@mcp.tool()
def chipagent_coordinate_hw_sw_codesign(
    module_name: str,
    reg_code: Optional[str] = None,
    rtl_code: Optional[str] = None,
    header_code: Optional[str] = None,
    hal_code: Optional[str] = None,
    tb_code: Optional[str] = None,
    driver_code: Optional[str] = None
) -> str:
    """Coordinate HW/SW co-design workflow using multi-agent system.

    Orchestrates hardware, verification, and software agents to perform
    complete HW/SW co-design flow.

    Args:
        module_name: Top module name
        reg_code: Register definitions (Verilog)
        rtl_code: RTL code (Verilog)
        header_code: Register header (C)
        hal_code: HAL library (C)
        tb_code: Testbench (Verilog)
        driver_code: Linux driver (C)

    Returns:
        Coordinated workflow result with all artifacts and checks
    """
    from .agents import AgentCoordinator
    coordinator = AgentCoordinator()
    result = coordinator.execute_hw_sw_codesign({
        "module_name": module_name,
        "reg_code": reg_code or "",
        "rtl_code": rtl_code or "",
        "header_code": header_code or "",
        "hal_code": hal_code or "",
        "tb_code": tb_code or "",
        "driver_code": driver_code or "",
    })
    return _to_text(result)


@mcp.tool()
def chipagent_execute_agent_workflow(
    workflow: list
) -> str:
    """Execute a custom multi-agent workflow.

    Runs a sequence of tasks across hardware, verification, and software agents.

    Args:
        workflow: List of task dicts, each with:
            - agent: str (hardware/verification/software)
            - task_type: str
            - inputs: Dict[str, Any]

    Returns:
        Workflow execution result with all artifacts and errors
    """
    from .agents import AgentCoordinator
    coordinator = AgentCoordinator()
    result = coordinator.coordinate(workflow)
    return _to_text(result)


# =============================================================================
# End-to-end EDA Flow (1 tool)
# =============================================================================
@mcp.tool()
def chipagent_run_flow(
    reg_code: str,
    module_name: str = "dut",
    tb_code: Optional[str] = None,
    layout: Optional[str] = None,
    lvs_layout_netlist: Optional[str] = None,
    lvs_schematic_netlist: Optional[str] = None,
    output_dir: Optional[str] = None,
    run_formality: bool = True,
    run_physical: bool = False,
    physical_clock_port: str = "clk",
    physical_clock_period: float = 310.0,
    physical_timeout: int = 1800,
    physical_cache: bool = True,
    physical_clean: bool = False,
    physical_high_fanout_nets: Optional[List[str]] = None,
    physical_high_fanout_max: int = 8,
    physical_platform: str = "asap7",
    physical_platform_options: Optional[Dict[str, Any]] = None,
) -> str:
    """Run a reproducible RTL-to-checks EDA flow over supplied design artifacts.

    The flow does not generate RTL. It validates user-supplied RTL through the
    available tool wrappers and writes reports/scripts/netlists under output_dir.

    Args:
        reg_code: RTL code to analyze
        module_name: Top module name
        tb_code: Optional Verilog/SystemVerilog testbench; enables simulation
        layout: Optional layout/GDS-like content; enables Magic DRC
        lvs_layout_netlist: Optional extracted SPICE netlist for LVS
        lvs_schematic_netlist: Optional schematic SPICE netlist for LVS
        output_dir: Output directory; defaults to generated/flows/<module_name>
        run_formality: Compare original RTL against synthesized netlist when synthesis succeeds
        run_physical: Run a physical implementation flow
        physical_clock_port: Clock port used in the generated SDC
        physical_clock_period: SDC clock period, default 310
        physical_timeout: Physical flow timeout in seconds
        physical_cache: Reuse matching physical results when present
        physical_clean: Remove previous flow work tree before running physical flow
        physical_high_fanout_nets: Hierarchical net patterns to split with
            targeted buffers during physical implementation
        physical_high_fanout_max: Maximum fanout for those targeted nets
        physical_platform: Physical-design platform (default asap7)
        physical_platform_options: Backend-specific physical options

    Returns:
        Flow report with per-step results, trust metadata, and artifact paths
    """
    from datetime import datetime, timezone

    out = Path(output_dir or Path("generated") / "flows" / module_name)
    out.mkdir(parents=True, exist_ok=True)

    steps: Dict[str, Any] = {}
    artifacts: Dict[str, str] = {}

    def record(name: str, raw: str) -> Dict[str, Any]:
        data = json.loads(raw)
        steps[name] = data
        for key, path in (data.get("artifacts") or {}).items():
            artifacts[f"{name}.{key}"] = path
        return data

    if tb_code:
        record("simulation", chipagent_run_simulation(
            reg_code=reg_code,
            tb_code=tb_code,
            module_name=module_name,
            output_dir=str(out),
        ))
    else:
        steps["simulation"] = {"status": "skipped", "reason": "tb_code not provided"}

    synthesis = record("synthesis", chipagent_run_synthesis(
        reg_code=reg_code,
        module_name=module_name,
        output_dir=str(out),
    ))

    record("timing", chipagent_analyze_timing(
        reg_code=reg_code,
        module_name=module_name,
        output_dir=str(out),
    ))
    record("area", chipagent_optimize_area(
        reg_code=reg_code,
        module_name=module_name,
        output_dir=str(out),
    ))
    record("power", chipagent_analyze_power(
        reg_code=reg_code,
        module_name=module_name,
        output_dir=str(out),
    ))

    if run_formality and synthesis.get("status") == "success" and synthesis.get("netlist"):
        record("formality", chipagent_run_formality(
            reference_netlist=reg_code,
            implementation_netlist=synthesis["netlist"],
            module_name=module_name,
            output_dir=str(out),
        ))
    else:
        steps["formality"] = {
            "status": "skipped",
            "reason": "disabled or synthesis netlist unavailable",
        }

    if run_physical:
        record("physical", chipagent_run_physical_flow(
            reg_code=reg_code,
            rtl_files=None,
            module_name=module_name,
            platform=physical_platform,
            clock_port=physical_clock_port,
            clock_period=physical_clock_period,
            output_dir=str(out / "physical"),
            timeout=physical_timeout,
            cache=physical_cache,
            clean=physical_clean,
            high_fanout_nets=physical_high_fanout_nets,
            high_fanout_max=physical_high_fanout_max,
            platform_options=physical_platform_options,
        ))
    else:
        steps["physical"] = {
            "status": "skipped",
            "reason": "run_physical is false",
            "platform": physical_platform,
        }

    if layout:
        record("drc", chipagent_run_drc_check(
            layout=layout,
            module_name=module_name,
            output_dir=str(out),
        ))
    else:
        steps["drc"] = {"status": "skipped", "reason": "layout not provided"}

    if lvs_layout_netlist and lvs_schematic_netlist:
        record("lvs", chipagent_run_lvs_check(
            layout=lvs_layout_netlist,
            netlist=lvs_schematic_netlist,
            module_name=module_name,
            output_dir=str(out),
        ))
    else:
        steps["lvs"] = {"status": "skipped", "reason": "lvs netlists not provided"}

    classification = _classify_flow_steps(steps)
    failures = sorted(set(
        classification["design_failures"]
        + classification["infrastructure_failures"]
    ))

    summary = _flow_summary(steps, artifacts)

    report = {
        # ``status`` remains binary for compatibility with existing MCP
        # consumers. ``outcome`` explains whether failure came from the design
        # or from an incomplete EDA environment.
        "status": "failed" if failures else "success",
        "module_name": module_name,
        "output_dir": str(out),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failures": failures,
        **classification,
        "summary": summary,
        "steps": steps,
        "artifacts": artifacts,
    }
    report_path = out / f"{module_name}_flow_report.json"
    report_path.write_text(_to_text(report), encoding="utf-8")
    report["artifacts"]["flow_report.json"] = str(report_path)
    html_path = out / f"{module_name}_flow_summary.html"
    html_path.write_text(_flow_summary_html(report, html_path), encoding="utf-8")
    report["artifacts"]["flow_summary.html"] = str(html_path)
    return _to_text(report)


def _classify_flow_steps(steps: Dict[str, Any]) -> Dict[str, Any]:
    """Classify flow steps without conflating RTL and environment failures."""
    design_failures: list[str] = []
    infrastructure_failures: list[str] = []
    unavailable_steps: list[str] = []
    completed_steps: list[str] = []
    skipped_steps: list[str] = []

    for name, data in steps.items():
        status = str(data.get("status") or "")
        passed = data.get("passed")
        unavailable = (
            data.get("tool_available") is False
            or status == "unavailable"
        )
        failed = (
            status in {"error", "failed", "timeout"}
            or passed == "failed"
            or (name == "formality" and data.get("equivalent") is False)
            or (name == "lvs" and data.get("match") is False)
        )

        if status == "skipped":
            skipped_steps.append(name)
        elif unavailable:
            unavailable_steps.append(name)
            if failed:
                infrastructure_failures.append(name)
        elif failed:
            design_failures.append(name)
        else:
            completed_steps.append(name)

    if design_failures:
        outcome = "failed"
    elif infrastructure_failures and completed_steps:
        outcome = "partial"
    elif infrastructure_failures:
        outcome = "unavailable"
    else:
        outcome = "success"

    return {
        "outcome": outcome,
        "design_failures": sorted(set(design_failures)),
        "infrastructure_failures": sorted(set(infrastructure_failures)),
        "unavailable_steps": sorted(set(unavailable_steps)),
        "completed_steps": sorted(set(completed_steps)),
        "skipped_steps": sorted(set(skipped_steps)),
    }


@mcp.tool()
def chipagent_run_example_flow(
    example: str = "tiny_counter",
    output_dir: Optional[str] = None,
    run_physical: bool = True,
) -> str:
    """Run a built-in example through ChipAgent's end-to-end flow.

    This is intended as the first user-facing smoke test after environment
    setup. The default tiny_counter example can run all the way to ASAP7 DEF/GDS
    when run_physical is true.
    """
    examples = _builtin_flow_examples()
    if example not in examples:
        return _to_text({
            "status": "error",
            "message": f"Unknown example: {example}",
            "available_examples": sorted(examples),
        })
    data = examples[example]
    return chipagent_run_flow(
        reg_code=data["reg_code"],
        tb_code=data["tb_code"],
        module_name=data["module_name"],
        output_dir=output_dir or str(Path("generated") / "examples" / example),
        run_physical=run_physical,
        physical_clock_port=data.get("clock_port", "clk"),
    )


def _flow_summary(steps: Dict[str, Any], artifacts: Dict[str, str]) -> Dict[str, Any]:
    physical = steps.get("physical") or {}
    synthesis = steps.get("synthesis") or {}
    timing = steps.get("timing") or {}
    power = steps.get("power") or {}
    formality = steps.get("formality") or {}
    simulation = steps.get("simulation") or {}

    artifact_shortcuts = {
        "synthesis_netlist": artifacts.get("synthesis.netlist.v"),
        "physical_def": artifacts.get("physical.final_def"),
        "physical_gds": artifacts.get("physical.final_gds"),
        "physical_odb": artifacts.get("physical.final_odb"),
        "physical_run_log": artifacts.get("physical.run.log"),
    }
    artifact_shortcuts = {key: value for key, value in artifact_shortcuts.items() if value}

    trust = {}
    for name, data in steps.items():
        source = data.get("source")
        tool = data.get("tool")
        if source or tool:
            trust[name] = {"source": source, "tool": tool}

    return {
        "simulation": {
            "status": simulation.get("status") or simulation.get("passed"),
            "tool": simulation.get("tool"),
        },
        "synthesis": {
            "status": synthesis.get("status"),
            "tool": synthesis.get("tool"),
            "cells": synthesis.get("cells"),
            "source": synthesis.get("source"),
        },
        "timing": {
            "status": timing.get("status"),
            "source": timing.get("source"),
            "tool": timing.get("tool"),
            "slack": timing.get("slack"),
            "wns": timing.get("wns"),
            "tns": timing.get("tns"),
        },
        "power": {
            "status": power.get("status"),
            "source": power.get("source"),
            "total_power": power.get("total_power"),
            "dynamic_power": power.get("dynamic_power"),
            "leakage_power": power.get("leakage_power"),
        },
        "formality": {
            "status": formality.get("status"),
            "equivalent": formality.get("equivalent"),
        },
        "physical": {
            "status": physical.get("status"),
            "platform": physical.get("platform"),
            "cached": physical.get("cached"),
            "reproducibility": physical.get("reproducibility") or {},
            "qor": physical.get("qor") or {},
            "diagnosis": physical.get("diagnosis") or {},
            "has_def": bool((physical.get("artifacts") or {}).get("final_def")),
            "has_gds": bool((physical.get("artifacts") or {}).get("final_gds")),
        },
        "artifacts": artifact_shortcuts,
        "trust": trust,
    }


def _flow_summary_html(report: Dict[str, Any], html_path: Path) -> str:
    summary = report.get("summary") or {}
    rows = []
    for name in ("simulation", "synthesis", "timing", "power", "formality", "physical"):
        data = summary.get(name) or {}
        rows.append(
            "<tr>"
            f"<th>{_h(name)}</th>"
            f"<td>{_h(data.get('status') or data.get('equivalent') or '')}</td>"
            f"<td>{_h(data.get('tool') or data.get('platform') or data.get('source') or '')}</td>"
            f"<td><code>{_h(json.dumps(data, ensure_ascii=False, default=str))}</code></td>"
            "</tr>"
        )

    qor = (summary.get("physical") or {}).get("qor") or {}
    qor_items = "".join(
        f"<tr><th>{_h(key)}</th><td>{_h(value)}</td></tr>"
        for key, value in qor.items()
        if value is not None
    )
    artifacts = summary.get("artifacts") or {}
    artifact_items = "".join(
        f"<li><a href='{_href(path, html_path)}'>{_h(key)}</a><span>{_h(path)}</span></li>"
        for key, path in artifacts.items()
    )
    images = _flow_report_images(summary, html_path)
    image_items = "".join(
        f"<figure><img src='{_href(path, html_path)}' alt='{_h(Path(path).name)}'><figcaption>{_h(Path(path).name)}</figcaption></figure>"
        for path in images
    )
    diagnosis = ((summary.get("physical") or {}).get("diagnosis") or {})
    reproducibility = ((summary.get("physical") or {}).get("reproducibility") or {})
    repro_rows = "".join(
        f"<tr><th>{_h(key)}</th><td>{_h(value)}</td></tr>"
        for key, value in reproducibility.items()
        if key != "parameters"
    )
    parameters = reproducibility.get("parameters") or {}
    repro_rows += "".join(
        f"<tr><th>parameter.{_h(key)}</th><td>{_h(value)}</td></tr>"
        for key, value in parameters.items()
    )
    diagnosis_block = ""
    if diagnosis.get("status") and diagnosis.get("status") != "none":
        evidence = "".join(f"<li>{_h(line)}</li>" for line in diagnosis.get("evidence") or [])
        diagnosis_block = (
            "<section><h2>Diagnosis</h2>"
            f"<p><b>Stage:</b> {_h(diagnosis.get('stage'))}</p>"
            f"<p><b>Root cause:</b> {_h(diagnosis.get('root_cause'))}</p>"
            f"<p><b>Suggested fix:</b> {_h(diagnosis.get('suggested_fix'))}</p>"
            f"<ul>{evidence}</ul></section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ChipAgent Flow Summary - {_h(report.get('module_name'))}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 32px; color: #17202a; }}
    h1, h2 {{ margin: 0 0 12px; }}
    section {{ margin: 28px 0; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d7dde5; padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f6f8; width: 180px; }}
    code {{ white-space: pre-wrap; word-break: break-word; }}
    .status {{ display: inline-block; padding: 4px 8px; border-radius: 4px; background: #eef6ee; }}
    .failed {{ background: #fff1f0; }}
    ul.artifacts {{ padding-left: 20px; }}
    ul.artifacts span {{ display: block; color: #5d6d7e; font-size: 12px; margin: 2px 0 8px; }}
    .gallery {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
    figure {{ margin: 0; border: 1px solid #d7dde5; padding: 8px; }}
    img {{ max-width: 100%; height: auto; display: block; }}
    figcaption {{ font-size: 12px; color: #5d6d7e; margin-top: 6px; }}
  </style>
</head>
<body>
  <h1>ChipAgent Flow Summary</h1>
  <p><b>Module:</b> {_h(report.get('module_name'))}</p>
  <p><b>Status:</b> <span class="status {'failed' if report.get('status') != 'success' else ''}">{_h(report.get('status'))}</span></p>
  <p><b>Outcome:</b> {_h(report.get('outcome') or report.get('status'))}</p>
  <p><b>Design failures:</b> {_h(', '.join(report.get('design_failures') or []) or 'none')}</p>
  <p><b>Infrastructure failures:</b> {_h(', '.join(report.get('infrastructure_failures') or []) or 'none')}</p>
  <p><b>Unavailable steps:</b> {_h(', '.join(report.get('unavailable_steps') or []) or 'none')}</p>

  <section>
    <h2>Steps</h2>
    <table><tbody>{''.join(rows)}</tbody></table>
  </section>

  {diagnosis_block}

  <section>
    <h2>Physical QoR</h2>
    <table><tbody>{qor_items or '<tr><td>No physical QoR available.</td></tr>'}</tbody></table>
  </section>

  <section>
    <h2>Reproducibility</h2>
    <table><tbody>{repro_rows or '<tr><td>No reproducibility data available.</td></tr>'}</tbody></table>
  </section>

  <section>
    <h2>Artifacts</h2>
    <ul class="artifacts">{artifact_items}</ul>
  </section>

  <section>
    <h2>ORFS Images</h2>
    <div class="gallery">{image_items or '<p>No ORFS images available.</p>'}</div>
  </section>
</body>
</html>
"""


def _flow_report_images(summary: Dict[str, Any], html_path: Path) -> list[str]:
    run_log = (summary.get("artifacts") or {}).get("physical_run_log")
    if not run_log:
        return []
    reports_dir = Path(run_log).parent / "orfs-work" / "reports" / "base"
    if not reports_dir.exists():
        return []
    preferred = [
        "final_placement.webp",
        "final_routing.webp",
        "final_congestion.webp",
        "final_worst_path.webp",
        "final_ir_drop.webp",
        "cts_default_core_clock.webp",
    ]
    images = [reports_dir / name for name in preferred if (reports_dir / name).exists()]
    return [str(path) for path in images]


def _href(path: str, html_path: Path) -> str:
    try:
        rel = Path(path).resolve().relative_to(html_path.parent.resolve())
        return html.escape(str(rel))
    except Exception:
        return html.escape(path)


def _h(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _builtin_flow_examples() -> Dict[str, Dict[str, str]]:
    return {
        "tiny_counter": {
            "module_name": "tiny_counter",
            "clock_port": "clk",
            "reg_code": """
module tiny_counter(
  input clk,
  input rst_n,
  input en,
  output reg [3:0] count
);
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) count <= 4'd0;
    else if (en) count <= count + 4'd1;
  end
endmodule
""".strip(),
            "tb_code": """
module tb;
  reg clk = 0;
  reg rst_n = 0;
  reg en = 0;
  wire [3:0] count;

  tiny_counter dut(.clk(clk), .rst_n(rst_n), .en(en), .count(count));
  always #5 clk = ~clk;

  initial begin
    #12 rst_n = 1; en = 1;
    repeat (4) @(posedge clk);
    #1;
    if (count !== 4'd4) begin $display("FAIL count=%0d", count); $finish; end
    $display("PASS count=%0d", count);
    $finish;
  end
endmodule
""".strip(),
        }
    }


# =============================================================================
# Phase 3: Service Registry and Toolchain (3 tools)
# =============================================================================
@mcp.tool()
def chipagent_check_toolchain() -> str:
    """Inspect local EDA tool availability and return setup commands.

    Returns:
        Detected tools, missing tools, and Docker/apt/manual setup guidance.
    """
    from .toolchain import toolchain_status
    return _to_text(toolchain_status())


@mcp.tool()
def chipagent_list_available_tools(
    category: Optional[str] = None
) -> str:
    """List all available tools in the service registry.

    Args:
        category: Optional category filter (synthesis/physical/verification)

    Returns:
        List of available tools with metadata
    """
    from .registry import ServiceRegistry
    registry = ServiceRegistry()
    tools = registry.list_tools(category=category)
    return _to_text({
        "status": "success",
        "tools": [
            {
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "requires_eda": t.requires_eda,
                "eda_tools": t.eda_tools,
                "estimated_runtime": t.estimated_runtime,
            }
            for t in tools
        ],
        "count": len(tools),
    })


@mcp.tool()
def chipagent_discover_services(
    capability: Optional[str] = None
) -> str:
    """Discover services by capability or category.

    Args:
        capability: Capability to search for (e.g., "synthesis", "floorplan")

    Returns:
        List of services matching the capability
    """
    from .registry import ServiceRegistry, ServiceDiscovery
    registry = ServiceRegistry()
    discovery = ServiceDiscovery(registry)

    if capability:
        tools = discovery.recommend_tools_for_task(capability)
    else:
        tools = registry.list_tools()

    return _to_text({
        "status": "success",
        "capability": capability,
        "services": [
            {
                "name": t.name,
                "category": t.category,
                "description": t.description,
            }
            for t in tools
        ],
        "count": len(tools),
    })


# =============================================================================
# Phase 3: Security (2 tools)
# =============================================================================
@mcp.tool()
def chipagent_auth_status() -> str:
    """Check current authentication status.

    Returns:
        Authentication status with user information
    """
    from .security import get_authenticator, AuthenticationError

    authenticator = get_authenticator()

    # Check if authentication is enabled
    if os.environ.get("CHIPAGENT_AUTH_ENABLED", "false").lower() != "true":
        return _to_text({
            "status": "success",
            "authenticated": False,
            "message": "Authentication disabled",
        })

    # Try to get API key from environment
    api_key = os.environ.get("CHIPAGENT_API_KEY")
    if not api_key:
        return _to_text({
            "status": "success",
            "authenticated": False,
            "message": "No API key provided",
        })

    try:
        context = authenticator.authenticate_with_api_key(api_key)
        return _to_text({
            "status": "success",
            "authenticated": True,
            "user": {
                "user_id": context.user.user_id,
                "username": context.user.username,
                "role": context.user.role,
            },
        })
    except AuthenticationError as e:
        return _to_text({
            "status": "error",
            "authenticated": False,
            "message": str(e),
        })


@mcp.tool()
def chipagent_check_permission(
    tool_name: str
) -> str:
    """Check if current user has permission to access a tool.

    Args:
        tool_name: Name of the tool to check permission for

    Returns:
        Permission check result
    """
    from .security import get_authenticator, get_authorizer, AuthenticationError

    authenticator = get_authenticator()
    authorizer = get_authorizer()

    # Check if authentication is enabled
    if os.environ.get("CHIPAGENT_AUTH_ENABLED", "false").lower() != "true":
        return _to_text({
            "status": "success",
            "authorized": True,
            "message": "Authentication disabled, all access allowed",
        })

    # Get API key
    api_key = os.environ.get("CHIPAGENT_API_KEY")
    if not api_key:
        return _to_text({
            "status": "error",
            "authorized": False,
            "message": "No API key provided",
        })

    try:
        context = authenticator.authenticate_with_api_key(api_key)
        authorized = authorizer.can_access_tool(context, tool_name)

        return _to_text({
            "status": "success",
            "authorized": authorized,
            "tool": tool_name,
            "user": context.user.username,
            "role": context.user.role,
        })
    except AuthenticationError as e:
        return _to_text({
            "status": "error",
            "authorized": False,
            "message": str(e),
        })


# ======================================================================
# PPA Analysis Tools (Phase 3)
# ======================================================================
@mcp.tool()
def chipagent_estimate_area(
    reg_code: str,
    module_name: str = "dut"
) -> str:
    """Estimate area metrics from RTL code structure (no EDA tools required).

    Analyzes RTL structure to estimate:
    - Gate count (flip-flops, combinational logic, memory)
    - Complexity score (0-100)
    - Area breakdown by category

    Args:
        reg_code: RTL code (Verilog/SystemVerilog)
        module_name: Top module name (default: "dut")

    Returns:
        Area estimation report with gate count and complexity analysis
    """
    from .tools.base import ToolContext
    from .tools.ppa.area_estimator import AreaEstimatorTool
    from .models import TaskObject

    tool = AreaEstimatorTool()
    ctx = ToolContext(
        task=TaskObject(task_type="ppa_analysis", module_name=module_name, description=""),
        inputs={"rtl_code": reg_code},
    )
    result = tool.run(ctx)
    return _to_text(result.result)


@mcp.tool()
def chipagent_estimate_performance(
    reg_code: str,
    target_freq_mhz: Optional[float] = None,
    module_name: str = "dut",
    liberty: Optional[str] = None,
    liberty_file: Optional[str] = None,
    liberty_files: Optional[list[str]] = None,
    sdc: Optional[str] = None,
    clock_port: str = "clk",
    output_dir: Optional[str] = None,
) -> str:
    """Measure performance using Yosys technology mapping and OpenSTA.

    This API never fabricates Fmax from RTL text. A Liberty library and real
    Yosys/OpenSTA backends are required. ``target_freq_mhz`` is used to create
    a clock constraint when ``sdc`` is not supplied.

    Args:
        reg_code: RTL code (Verilog/SystemVerilog)
        target_freq_mhz: Target frequency for slack calculation (optional)
        module_name: Top module name (default: "dut")

    Returns:
        Performance estimation report with frequency and timing analysis
    """
    from .tools.base import ToolContext
    from .tools.synth_timing import TimingAnalysisTool
    from .models import TaskObject

    tool = TimingAnalysisTool(require_sta=True)
    ctx = ToolContext(
        task=TaskObject(task_type="ppa_analysis", module_name=module_name, description=""),
        inputs={
            "reg_code": reg_code,
            "target_freq_mhz": target_freq_mhz,
            "liberty": liberty or "",
            "liberty_file": liberty_file or "",
            "liberty_files": liberty_files or [],
            "sdc": sdc or "",
            "clock_port": clock_port,
            "output_dir": output_dir,
        },
    )
    result = tool.run(ctx)
    return _to_text(result.result)


@mcp.tool()
def chipagent_estimate_power(
    reg_code: str,
    clock_freq_mhz: float = 100.0,
    module_name: str = "dut"
) -> str:
    """Estimate power consumption from RTL code structure (no EDA tools required).

    Analyzes activity and structure to estimate:
    - Dynamic power (switching activity)
    - Static power (leakage)
    - Power breakdown by category

    Args:
        reg_code: RTL code (Verilog/SystemVerilog)
        clock_freq_mhz: Clock frequency in MHz (default: 100.0)
        module_name: Top module name (default: "dut")

    Returns:
        Power estimation report with dynamic/static power breakdown
    """
    from .tools.base import ToolContext
    from .tools.ppa.power_estimator import PowerEstimatorTool
    from .models import TaskObject

    tool = PowerEstimatorTool()
    ctx = ToolContext(
        task=TaskObject(task_type="ppa_analysis", module_name=module_name, description=""),
        inputs={
            "rtl_code": reg_code,
            "clock_freq_mhz": clock_freq_mhz,
        },
    )
    result = tool.run(ctx)
    return _to_text(result.result)


@mcp.tool()
def chipagent_check_ppa_targets(
    reg_code: str,
    max_gates: Optional[int] = None,
    min_freq_mhz: Optional[float] = None,
    max_power_mw: Optional[float] = None,
    clock_freq_mhz: float = 100.0,
    module_name: str = "dut",
    liberty: Optional[str] = None,
    liberty_file: Optional[str] = None,
    liberty_files: Optional[list[str]] = None,
    sdc: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> str:
    """Comprehensive PPA analysis with target checking and optimization suggestions.

    Runs all PPA estimators and checks against targets:
    - Area: estimated gates vs max_gates
    - Performance: estimated frequency vs min_freq_mhz
    - Power: estimated power vs max_power_mw

    Provides optimization suggestions if targets are not met.

    Args:
        reg_code: RTL code (Verilog/SystemVerilog)
        max_gates: Maximum gate count target (optional)
        min_freq_mhz: Minimum clock frequency target (optional)
        max_power_mw: Maximum power consumption target (optional)
        clock_freq_mhz: Clock frequency for power estimation (default: 100.0)
        module_name: Top module name (default: "dut")

    Returns:
        Comprehensive PPA report with pass/fail status and optimization suggestions
    """
    from .tools.base import ToolContext
    from .tools.ppa.ppa_checker import PPATargetCheckerTool
    from .models import TaskObject

    # Build targets dict
    targets = {}
    if max_gates is not None:
        targets["max_gates"] = max_gates
    if min_freq_mhz is not None:
        targets["min_freq_mhz"] = min_freq_mhz
    if max_power_mw is not None:
        targets["max_power_mw"] = max_power_mw
    targets["clock_freq_mhz"] = clock_freq_mhz

    tool = PPATargetCheckerTool()
    ctx = ToolContext(
        task=TaskObject(task_type="ppa_analysis", module_name=module_name, description=""),
        inputs={
            "rtl_code": reg_code,
            "targets": targets,
            "liberty": liberty or "",
            "liberty_file": liberty_file or "",
            "liberty_files": liberty_files or [],
            "sdc": sdc or "",
            "output_dir": output_dir,
        },
    )
    result = tool.run(ctx)
    return _to_text(result.result)


def _list_tools_catalogue() -> str:
    """Print the tool catalogue (for `--list`)."""
    tools = []
    for name, info in (mcp._tool_manager._tools.items()  # type: ignore[attr-defined]
                       if hasattr(mcp, "_tool_manager") else []):
        tools.append(name)
    return _to_text(tools)


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="ChipAgent MCP server (Claude Code integration)")
    p.add_argument("--list", action="store_true", help="Print the tool catalogue and exit")
    args = p.parse_args()
    if args.list:
        # FastMCP exposes tools via the manager; fall back gracefully.
        try:
            tools = mcp._tool_manager._tools  # type: ignore[attr-defined]
            print(_to_text(list(tools.keys())))
        except Exception:
            print("(tool catalogue unavailable in this FastMCP version)")
        return
    # Stdio transport — what Claude Code connects to.
    mcp.run()


if __name__ == "__main__":
    main()
