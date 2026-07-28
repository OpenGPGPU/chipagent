"""Timing analysis tool using OpenSTA (Phase 3 Step 1).

Performs static timing analysis on synthesized netlists.
Uses OpenSTA for real timing; Yosys-only results are explicitly marked as estimates.
"""
import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

from chipagent.toolchain import run_eda_command, which_tool
from chipagent.tools.base import Tool, ToolContext, ToolResult, persist_tool_artifacts, trust_metadata


def _liberty_scalar(text: str, name: str) -> str | None:
    match = re.search(rf"\b{re.escape(name)}\s*:\s*([^;]+);", text)
    return match.group(1).strip() if match else None


def _liberty_cell_blocks(text: str) -> list[str]:
    """Extract complete top-level cell blocks from a Liberty library."""
    blocks: list[str] = []
    for match in re.finditer(r"(?m)^\s*cell\s*\([^)]*\)\s*\{", text):
        depth = 0
        in_string = False
        escaped = False
        for index in range(match.start(), len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[match.start():index + 1])
                    break
    return blocks


def _merge_liberty_texts(texts: list[str]) -> str:
    """Merge split cell-family libraries after checking their PVT metadata."""
    if not texts:
        raise ValueError("No Liberty libraries supplied")
    reference = {
        name: _liberty_scalar(texts[0], name)
        for name in ("nom_voltage", "nom_temperature")
    }
    merged = texts[0]
    insert_at = merged.rfind("}")
    if insert_at < 0:
        raise ValueError("Malformed base Liberty library")
    additions: list[str] = []
    for text in texts[1:]:
        for name, expected in reference.items():
            actual = _liberty_scalar(text, name)
            if expected and actual and actual != expected:
                raise ValueError(
                    f"Liberty PVT mismatch for {name}: {expected} vs {actual}"
                )
        additions.extend(_liberty_cell_blocks(text))
    if additions:
        merged = merged[:insert_at] + "\n" + "\n".join(additions) + "\n" + merged[insert_at:]
    return merged


class TimingAnalysisTool(Tool):
    name = "analyze_timing"

    def __init__(self, require_sta: bool = False):
        self.require_sta = require_sta

    def run(self, ctx: ToolContext) -> ToolResult:
        netlist = ctx.inputs.get("netlist", "").strip()
        reg_code = ctx.inputs.get("reg_code", "").strip()

        code = netlist or reg_code
        if not code:
            return ToolResult(
                result={"status": "error", "message": "No netlist or reg_code provided"},
                issues=["Missing input: netlist or reg_code"],
            )

        sta_available = which_tool("sta") or run_eda_command(["sta", "-version"], timeout=30).returncode == 0
        yosys_available = which_tool("yosys") or run_eda_command(["yosys", "-V"], timeout=30).returncode == 0
        liberty = (ctx.inputs.get("liberty") or "").strip()
        liberty_file = (ctx.inputs.get("liberty_file") or "").strip()
        liberty_files = ctx.inputs.get("liberty_files") or []

        if self.require_sta and not (liberty or liberty_file or liberty_files):
            return ToolResult(
                result={
                    "status": "error",
                    "message": "A Liberty library is required for real performance measurement",
                    "required_inputs": ["liberty or liberty_file"],
                    **trust_metadata(source="tool", tool="opensta", tool_available=bool(sta_available)),
                },
                issues=["Missing Liberty timing library"],
            )

        if self.require_sta and not yosys_available:
            return ToolResult(
                result={
                    "status": "error",
                    "message": "Real timing measurement requires Yosys; it is unavailable on the host and in Docker",
                    "required_tools": ["yosys"],
                    **trust_metadata(source="tool", tool="yosys", tool_available=False),
                },
                issues=["Missing timing tool: yosys"],
            )

        if sta_available and yosys_available and (liberty or liberty_file or liberty_files):
            sta_result = self._run_opensta(code, ctx)
            if sta_result.result.get("status") == "success":
                return sta_result
            fallback = self._run_yosys_abc_timing(code, ctx)
            fallback.issues.insert(0, "OpenSTA failed; used real Yosys/ABC Liberty timing")
            return fallback

        # ABC is a real technology mapper and timing engine.  Its `stime`
        # result uses Liberty cell arcs, but has no SDC clock checks or routed
        # parasitics, so report it as a pre-STA combinational measurement.
        if yosys_available and (liberty or liberty_file or liberty_files):
            return self._run_yosys_abc_timing(code, ctx)

        # Try yosys stat for basic info
        if yosys_available:
            return self._run_yosys_stat(code, ctx)

        # No tools available - return error
        return ToolResult(
            result={
                "status": "error",
                "message": "No timing analysis tools available. Please install OpenSTA (https://github.com/The-OpenROAD-Project/OpenSTA) or Yosys (https://yosyshq.net/yosys/)",
                "required_tools": ["sta", "yosys"],
                **trust_metadata(source="tool", tool_available=False),
            },
            issues=["OpenSTA and Yosys not available"],
        )

    def _run_yosys_abc_timing(self, code: str, ctx: ToolContext) -> ToolResult:
        """Measure mapped combinational delay with Yosys/ABC and Liberty."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "design.sv").write_text(code)

            liberty = (ctx.inputs.get("liberty") or "").strip()
            liberty_file = (ctx.inputs.get("liberty_file") or "").strip()
            liberty_files = list(ctx.inputs.get("liberty_files") or [])
            if liberty:
                (tmpdir / "library.lib").write_text(liberty)
            else:
                source_paths = [Path(path) for path in liberty_files]
                if liberty_file:
                    source_paths.insert(0, Path(liberty_file))
                missing = [str(path) for path in source_paths if not path.exists()]
                if missing:
                    return ToolResult(
                        result={"status": "error", "message": f"Liberty file not found: {missing[0]}"},
                        issues=[f"Liberty file not found: {missing[0]}"],
                    )
                try:
                    merged_liberty = _merge_liberty_texts(
                        [path.read_text() for path in source_paths]
                    )
                except ValueError as exc:
                    return ToolResult(
                        result={"status": "error", "message": str(exc)},
                        issues=["Liberty merge validation failed"],
                    )
                (tmpdir / "library.lib").write_text(merged_liberty)

            top_module = ctx.task.module_name or ctx.inputs.get("module_name") or "top"
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module):
                return ToolResult(
                    result={"status": "error", "message": f"Invalid module_name: {top_module}"},
                    issues=["Invalid input: module_name"],
                )

            abc_script = """\
strash
ifraig
scorr
dc2
dretime
retime
strash
&get -n
&dch -f
&nf
&put
stime
"""
            (tmpdir / "abc_timing.script").write_text(abc_script)
            yosys_script = f"""\
read_verilog -sv design.sv
hierarchy -check -top {top_module}
proc
flatten
opt
techmap
opt
abc -liberty library.lib -script abc_timing.script -showtmp
clean
stat -liberty library.lib
write_verilog -noattr mapped.v
"""
            (tmpdir / "timing.ys").write_text(yosys_script)
            command = ["yosys", "-s", "timing.ys"]
            run = run_eda_command(command, work_dir=str(tmpdir), timeout=120)
            combined = f"{run.stdout}\n{run.stderr}"
            if run.returncode != 0:
                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "failed_report.log": combined,
                        "failed_timing.ys": yosys_script,
                        "failed_abc_timing.script": abc_script,
                        "failed_design.sv": code,
                        "failed_library.lib": (tmpdir / "library.lib").read_text(),
                    },
                )
                return ToolResult(
                    result={
                        "status": "failed",
                        "message": "Yosys/ABC technology-mapped timing failed",
                        "stdout": run.stdout,
                        "stderr": run.stderr,
                        **trust_metadata(source="tool", tool="yosys+abc", tool_available=True,
                                         command=run.command or command, artifacts=artifacts),
                    },
                    issues=["Yosys/ABC timing failed"],
                )

            delay_matches = re.findall(r"\bDelay\s*=\s*([\d.]+)\s*ps\b", combined)
            if not delay_matches:
                return ToolResult(
                    result={
                        "status": "failed",
                        "message": "ABC completed but did not report a Liberty timing delay",
                        **trust_metadata(source="tool", tool="yosys+abc", tool_available=True,
                                         command=run.command or command),
                    },
                    issues=["ABC timing report contained no delay"],
                )

            delay_ps = max(float(value) for value in delay_matches)
            fmax_mhz = 1_000_000.0 / delay_ps
            target_freq_mhz = ctx.inputs.get("target_freq_mhz")
            target_period_ps = 1_000_000.0 / float(target_freq_mhz) if target_freq_mhz else None
            budget_slack_ps = target_period_ps - delay_ps if target_period_ps else None
            cell_match = re.search(r"Number of cells:\s+(\d+)", combined)
            area_match = re.search(r"Chip area for module .*?:\s*([\d.]+)", combined)

            artifacts = persist_tool_artifacts(
                ctx,
                self.name,
                {
                    "report.log": combined,
                    "timing.ys": yosys_script,
                    "abc_timing.script": abc_script,
                    "mapped.v": (tmpdir / "mapped.v").read_text(),
                    "library.lib": (tmpdir / "library.lib").read_text(),
                },
            )
            return ToolResult(
                result={
                    "status": "success",
                    **trust_metadata(
                        source="tool",
                        tool="yosys+abc",
                        tool_available=True,
                        command=run.command or command,
                        artifacts=artifacts,
                    ),
                    "analysis_level": "technology_mapped_combinational",
                    "liberty_components": [
                        component
                        for component in [liberty_file, *liberty_files]
                        if component
                    ] if not liberty else ["inline"],
                    "rtl_sha256": hashlib.sha256(code.encode()).hexdigest(),
                    "liberty_sha256": hashlib.sha256(
                        (tmpdir / "library.lib").read_bytes()
                    ).hexdigest(),
                    "corner": {
                        "nom_voltage": _liberty_scalar(
                            (tmpdir / "library.lib").read_text(), "nom_voltage"
                        ),
                        "nom_temperature": _liberty_scalar(
                            (tmpdir / "library.lib").read_text(), "nom_temperature"
                        ),
                    },
                    "wire_load": "as defined by Liberty/ABC; no routed parasitics",
                    "critical_path_delay_ps": delay_ps,
                    "measured_fmax_mhz": fmax_mhz,
                    "target_freq_mhz": target_freq_mhz,
                    "target_period_ps": target_period_ps,
                    "combinational_budget_slack_ps": budget_slack_ps,
                    "cell_count": int(cell_match.group(1)) if cell_match else None,
                    "cell_area": float(area_match.group(1)) if area_match else None,
                    "limitations": [
                        "No OpenSTA SDC setup/hold analysis",
                        "No post-route parasitics",
                        "Fmax is the reciprocal of mapped combinational delay",
                    ],
                },
                issues=["OpenSTA unavailable; result is real pre-STA Yosys/ABC Liberty timing"],
            )

    def _run_opensta(self, code: str, ctx: ToolContext) -> ToolResult:
        """Run OpenSTA timing analysis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            rtl_file = tmpdir / "design.sv"
            rtl_file.write_text(code)

            liberty = (ctx.inputs.get("liberty") or "").strip()
            liberty_file = (ctx.inputs.get("liberty_file") or "").strip()
            liberty_files = list(ctx.inputs.get("liberty_files") or [])
            if liberty:
                library_file = tmpdir / "library.lib"
                library_file.write_text(liberty)
            else:
                source_paths = [Path(path) for path in liberty_files]
                if liberty_file:
                    source_paths.insert(0, Path(liberty_file))
                missing = [str(path) for path in source_paths if not path.exists()]
                if missing:
                    return ToolResult(
                        result={
                            "status": "error",
                            "message": f"Liberty file not found: {missing[0]}",
                            **trust_metadata(source="tool", tool="opensta", tool_available=True),
                        },
                        issues=[f"Liberty file not found: {missing[0]}"],
                    )
                library_file = tmpdir / "library.lib"
                try:
                    library_file.write_text(_merge_liberty_texts(
                        [path.read_text() for path in source_paths]
                    ))
                except ValueError as exc:
                    return ToolResult(
                        result={"status": "error", "message": str(exc)},
                        issues=["Liberty merge validation failed"],
                    )

            top_module = ctx.task.module_name or ctx.inputs.get("module_name") or "top"
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module):
                return ToolResult(
                    result={"status": "error", "message": f"Invalid module_name: {top_module}"},
                    issues=["Invalid input: module_name"],
                )

            target_freq_mhz = ctx.inputs.get("target_freq_mhz")
            default_period_ns = 1000.0 / float(target_freq_mhz) if target_freq_mhz else 10.0
            sdc_file = tmpdir / "timing.sdc"
            sdc_file.write_text((ctx.inputs.get("sdc") or f"""
create_clock -name clk -period {default_period_ns:.6f} [get_ports clk]
set_clock_uncertainty 0.1 [get_clocks clk]
""").strip() + "\n")

            yosys_script = f"""
read_verilog -sv design.sv
hierarchy -check -top {top_module}
proc
flatten
opt
techmap
opt
dfflibmap -liberty library.lib
abc -liberty library.lib
clean
stat -liberty library.lib
write_verilog -noattr -noexpr netlist.v
"""
            yosys_file = tmpdir / "synth.ys"
            yosys_file.write_text(yosys_script)

            synth_command = ["yosys", "-s", yosys_file.name]
            synth_result = run_eda_command(synth_command, work_dir=str(tmpdir), timeout=120)
            if synth_result.returncode != 0:
                return ToolResult(
                    result={
                        "status": "failed",
                        "message": "Yosys technology mapping failed",
                        "stderr": synth_result.stderr,
                        **trust_metadata(source="tool", tool="yosys", tool_available=True,
                                         command=synth_result.command or synth_command),
                    },
                    issues=["Yosys technology mapping failed"],
                )

            # Write STA script
            sta_script = f"""
read_liberty library.lib
read_verilog netlist.v
link_design {top_module}
set_cmd_units -time ns
read_sdc timing.sdc
check_setup -verbose
report_checks -path_delay max -format full_clock_expanded -digits 6
report_tns
report_wns
"""
            script_file = tmpdir / "timing.sta"
            script_file.write_text(sta_script)

            try:
                command = ["sta", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                if result.returncode != 0:
                    return ToolResult(
                        result={
                            "status": "failed",
                            "stderr": result.stderr,
                            **trust_metadata(
                                source="tool",
                                tool="opensta",
                                tool_available=True,
                                command=result.command or command,
                            ),
                        },
                        issues=["OpenSTA analysis failed"],
                    )

                # Parse timing results
                timing = self._parse_sta_output(result.stdout)
                delay_ns = timing.get("critical_path_delay_ns")
                slack_ns = timing.get("slack")
                target_period_ns = (
                    1000.0 / float(target_freq_mhz) if target_freq_mhz else None
                )
                constraint_overhead_ns = (
                    target_period_ns - delay_ns - slack_ns
                    if target_period_ns is not None
                    and delay_ns is not None
                    and slack_ns is not None
                    else None
                )
                constraint_aware_fmax_mhz = (
                    1000.0 / (delay_ns + max(0.0, constraint_overhead_ns))
                    if delay_ns
                    and constraint_overhead_ns is not None
                    else None
                )
                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "report.log": result.stdout,
                        "synthesis.log": synth_result.stdout,
                        "synth.ys": yosys_script,
                        "netlist.v": (tmpdir / "netlist.v").read_text(),
                        "timing.sta": sta_script,
                        "timing.sdc": sdc_file.read_text(),
                    },
                )

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="tool",
                            tool="opensta",
                            tool_available=True,
                            command=command,
                            artifacts=artifacts,
                        ),
                        "analysis_level": "pre_route_sta",
                        "liberty_components": [
                            component
                            for component in [liberty_file, *liberty_files]
                            if component
                        ] if not liberty else ["inline"],
                        "rtl_sha256": hashlib.sha256(code.encode()).hexdigest(),
                        "liberty_sha256": hashlib.sha256(library_file.read_bytes()).hexdigest(),
                        "corner": {
                            "nom_voltage": _liberty_scalar(
                                library_file.read_text(), "nom_voltage"
                            ),
                            "nom_temperature": _liberty_scalar(
                                library_file.read_text(), "nom_temperature"
                            ),
                        },
                        "slack": timing.get("slack", 0.0),
                        "wns": timing.get("wns", 0.0),
                        "tns": timing.get("tns", 0.0),
                        "critical_path": timing.get("critical_path", ""),
                        "critical_path_delay_ns": timing.get("critical_path_delay_ns"),
                        "measured_fmax_mhz": (
                            1000.0 / timing["critical_path_delay_ns"]
                            if timing.get("critical_path_delay_ns")
                            else None
                        ),
                        "constraint_overhead_ns": constraint_overhead_ns,
                        "constraint_aware_fmax_mhz": constraint_aware_fmax_mhz,
                        "target_freq_mhz": target_freq_mhz,
                        "limitations": [
                            "No post-route SPEF parasitics",
                            "Virtual block clock; no sequential launch/capture cells",
                        ],
                        "report": result.stdout,
                    },
                    issues=[],
                )

            except subprocess.TimeoutExpired:
                return ToolResult(
                    result={
                        "status": "timeout",
                        **trust_metadata(source="tool", tool="opensta", tool_available=True),
                    },
                    issues=["OpenSTA analysis timed out"],
                )
            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="tool", tool="opensta", tool_available=True),
                    },
                    issues=[f"OpenSTA execution error: {e}"],
                )

    def _run_yosys_stat(self, code: str, ctx: ToolContext) -> ToolResult:
        """Use yosys stat for basic structural estimation, not real STA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            input_file = tmpdir / "input.v"
            input_file.write_text(code)

            top_module = ctx.task.module_name or ctx.inputs.get("module_name") or "top"
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module):
                return ToolResult(
                    result={"status": "error", "message": f"Invalid module_name: {top_module}"},
                    issues=["Invalid input: module_name"],
                )

            yosys_script = f"""
read_verilog input.v
synth -top {top_module}
stat
"""
            script_file = tmpdir / "stat.ys"
            script_file.write_text(yosys_script)

            try:
                command = ["yosys", "-s", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                cells = 0
                m = re.search(r"Number of cells:\s+(\d+)", result.stdout)
                if m:
                    cells = int(m.group(1))

                if result.returncode != 0:
                    return ToolResult(
                        result={
                            "status": "failed",
                            "stderr": result.stderr,
                            **trust_metadata(
                                source="static_estimate",
                                tool="yosys",
                                tool_available=True,
                                command=result.command or command,
                            ),
                        },
                        issues=["Yosys stat failed"],
                    )

                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "report.log": result.stdout,
                        "stat.ys": yosys_script,
                    },
                )

                return ToolResult(
                    result={
                        "status": "success",
                        **trust_metadata(
                            source="static_estimate",
                            tool="yosys",
                            tool_available=True,
                            command=result.command or command,
                            artifacts=artifacts,
                        ),
                        "slack": None,
                        "wns": None,
                        "tns": None,
                        "critical_path": "",
                        "cell_count": cells,
                        "report": result.stdout,
                    },
                    issues=["OpenSTA not available; timing result is a Yosys structural estimate"],
                )

            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="static_estimate", tool="yosys", tool_available=True),
                    },
                    issues=[f"Yosys execution error: {e}"],
                )

    def _parse_sta_output(self, output: str) -> dict:
        """Parse OpenSTA report output."""
        timing = {"slack": None, "wns": None, "tns": None, "critical_path": ""}

        # slack (required time - arrival time)
        m = re.search(
            r"(?:slack(?:\s+\([A-Z]+\))?\s+([-\d.]+)|"
            r"([-\d.]+)\s+slack(?:\s+\([A-Z]+\))?)",
            output,
            re.IGNORECASE,
        )
        if m:
            timing["slack"] = float(m.group(1) or m.group(2))

        # wns (worst negative slack)
        m = re.search(r"wns(?:\s+\w+)?\s+([-\d.]+)", output, re.IGNORECASE)
        if m:
            timing["wns"] = float(m.group(1))

        # tns (total negative slack)
        m = re.search(r"tns(?:\s+\w+)?\s+([-\d.]+)", output, re.IGNORECASE)
        if m:
            timing["tns"] = float(m.group(1))

        # critical path (first path mentioned)
        m = re.search(r"Path:\s+([^\n]+)", output)
        if m:
            timing["critical_path"] = m.group(1).strip()
        else:
            start = re.search(r"Startpoint:\s+([^\s]+)", output)
            end = re.search(r"Endpoint:\s+([^\s]+)", output)
            if start and end:
                timing["critical_path"] = f"{start.group(1)} -> {end.group(1)}"

        arrivals = [
            float(value)
            for value in re.findall(
                r"^\s*([-\d.]+)\s+data arrival time\s*$",
                output,
                re.IGNORECASE | re.MULTILINE,
            )
        ]
        if arrivals:
            timing["critical_path_delay_ns"] = max(arrivals)

        return timing
