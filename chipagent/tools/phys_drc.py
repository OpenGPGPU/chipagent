"""Design Rule Check tool using Magic (Phase 3 Step 2).

Performs DRC checks on layout to ensure manufacturability.
Returns an explicit error when Magic is unavailable.
"""
import re
import tempfile
from pathlib import Path

from chipagent.toolchain import run_eda_command, which_tool
from chipagent.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    missing_tool_result,
    persist_tool_artifacts,
    trust_metadata,
)


class DRCCheckTool(Tool):
    name = "run_drc_check"

    def run(self, ctx: ToolContext) -> ToolResult:
        layout = ctx.inputs.get("layout", "").strip()
        netlist = ctx.inputs.get("netlist", "").strip()

        code = layout or netlist
        if not code:
            return ToolResult(
                result={"status": "error", "message": "No layout or netlist provided"},
                issues=["Missing input: layout or netlist"],
            )

        if not which_tool("magic"):
            probe = run_eda_command(["magic", "--version"], timeout=30)
            if probe.returncode != 0:
                message = probe.stderr.strip() or "Magic not available on host or Docker tool image."
                if probe.mode == "unavailable":
                    message = "Magic is not installed and Docker tool image is unavailable. Run: bash scripts/setup_eda_env.sh --docker"
                return missing_tool_result("magic", message, install_url="http://opencircuitdesign.com/magic/")

        return self._run_magic_drc(code, ctx)

    def _run_magic_drc(self, code: str, ctx: ToolContext) -> ToolResult:
        """Run Magic DRC."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write layout (assume GDS or DEF format)
            layout_file = tmpdir / "layout.gds"
            layout_file.write_text(code)

            # Write Magic script
            magic_script = f"""
gds read layout.gds
load top
drc check
drc count
drc catchup
drc count
quit
"""
            script_file = tmpdir / "drc.tcl"
            script_file.write_text(magic_script)

            try:
                command = ["magic", "-dnull", "-noconsole", "-rcfile", "/dev/null", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                if result.returncode != 0:
                    return ToolResult(
                        result={
                            "status": "failed",
                            "stderr": result.stderr,
                            **trust_metadata(
                                source="tool",
                                tool="magic",
                                tool_available=True,
                                command=result.command or command,
                            ),
                        },
                        issues=["Magic DRC failed"],
                    )

                # Parse DRC results
                drc = self._parse_magic_output(result.stdout)

                violations = drc.get("violations", [])
                violation_count = len(violations)
                artifacts = persist_tool_artifacts(ctx, self.name, {
                    "report.log": result.stdout,
                    "stderr.log": result.stderr,
                    "drc.tcl": magic_script,
                })

                return ToolResult(
                    result={
                        "status": "passed" if violation_count == 0 else "failed",
                        **trust_metadata(
                            source="tool",
                            tool="magic",
                            tool_available=True,
                            command=result.command or command,
                            artifacts=artifacts,
                        ),
                        "violations": violations,
                        "violation_count": violation_count,
                        "categories": list(set(v.get("type", "unknown") for v in violations)),
                        "report": result.stdout,
                    },
                    issues=violations if violation_count > 0 else [],
                )

            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="tool", tool="magic", tool_available=True),
                    },
                    issues=[f"Magic execution error: {e}"],
                )

    def _parse_magic_output(self, output: str) -> dict:
        """Parse Magic DRC output."""
        drc = {"violations": []}

        # Parse DRC violations
        # Example: "Spacing violation at (100, 200)"
        for m in re.finditer(r"(\w+ violation) at \((\d+), (\d+)\)", output):
            drc["violations"].append({
                "type": m.group(1),
                "location": f"({m.group(2)}, {m.group(3)})",
                "description": f"{m.group(1)} at ({m.group(2)}, {m.group(3)})",
            })

        # Also check for "DRC errors: N"
        m = re.search(r"DRC errors:\s*(\d+)", output)
        if m:
            count = int(m.group(1))
            # If we didn't parse individual violations, create generic ones
            if len(drc["violations"]) < count:
                for i in range(count - len(drc["violations"])):
                    drc["violations"].append({
                        "type": "drc_error",
                        "location": "unknown",
                        "description": f"DRC error {i+1}",
                    })

        return drc
