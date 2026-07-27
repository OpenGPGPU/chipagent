"""Formal verification tool using Yosys equiv_check (Phase 3 Step 1).

Performs equivalence checking between reference and implementation netlists.
Returns an explicit error when Yosys is unavailable.
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


class FormalVerifyTool(Tool):
    name = "run_formality"

    def run(self, ctx: ToolContext) -> ToolResult:
        reference = ctx.inputs.get("reference_netlist", "").strip()
        implementation = ctx.inputs.get("implementation_netlist", "").strip()

        if not reference or not implementation:
            return ToolResult(
                result={"status": "error", "message": "Missing reference or implementation netlist"},
                issues=["Missing input: reference_netlist or implementation_netlist"],
            )

        if not which_tool("yosys"):
            probe = run_eda_command(["yosys", "-V"], timeout=30)
            if probe.returncode != 0:
                message = probe.stderr.strip() or "Yosys not available on host or Docker tool image."
                if probe.mode == "unavailable":
                    message = "Yosys not installed and Docker tool image is unavailable. Run: bash scripts/setup_eda_env.sh --docker"
                return missing_tool_result("yosys", message, install_url="https://yosyshq.net/yosys/")

        top_module = ctx.task.module_name or ctx.inputs.get("module_name") or "top"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module):
            return ToolResult(
                result={"status": "error", "message": f"Invalid module_name: {top_module}"},
                issues=["Invalid input: module_name"],
            )

        return self._run_yosys_equiv(reference, implementation, top_module, ctx)

    def _run_yosys_equiv(self, reference: str, implementation: str, top_module: str, ctx: ToolContext) -> ToolResult:
        """Run Yosys equivalence checking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write reference
            ref_file = tmpdir / "reference.v"
            ref_file.write_text(reference)

            # Write implementation
            impl_file = tmpdir / "implementation.v"
            impl_file.write_text(implementation)

            # Write equivalence check script
            yosys_script = f"""
# Read reference design
read_verilog reference.v
prep -top {top_module}
async2sync
opt_clean
design -stash gold_design

# Read implementation design
read_verilog implementation.v
prep -top {top_module}
async2sync
opt_clean
design -stash gate_design

# Restore both designs under stable names
design -reset
design -copy-from gold_design -as gold {top_module}
design -copy-from gate_design -as gate {top_module}

# Perform equivalence check
equiv_make gold gate equiv
hierarchy -top equiv
proc
opt_clean
equiv_simple
equiv_induct
equiv_status -assert
"""
            script_file = tmpdir / "equiv.ys"
            script_file.write_text(yosys_script)

            try:
                command = ["yosys", "-s", script_file.name]
                result = run_eda_command(command, work_dir=str(tmpdir), timeout=60)

                report = result.stdout
                if result.stderr:
                    report = f"{result.stdout}\n\n[stderr]\n{result.stderr}"

                # Check if equivalence was proven
                equivalent = result.returncode == 0 and (
                    "Equivalence successfully proven" in result.stdout
                    or "Proved 0 previously unproven $equiv cells" in result.stdout
                )

                # Parse final mismatches from equiv_status, not intermediate proof attempts.
                mismatches = []
                final = re.search(r"Of those cells\s+\d+\s+are proven and\s+(\d+)\s+are unproven", report)
                if final and int(final.group(1)) > 0:
                    mismatches.append(f"{int(final.group(1))} unproven equivalence cells")
                if result.returncode != 0 and result.stderr.strip():
                    first_error = next(
                        (line.strip() for line in result.stderr.splitlines() if line.strip()),
                        "Yosys equivalence check failed",
                    )
                    mismatches.append(first_error)
                artifacts = persist_tool_artifacts(
                    ctx,
                    self.name,
                    {
                        "reference.v": reference,
                        "implementation.v": implementation,
                        "equiv.ys": yosys_script,
                        "report.log": report,
                    },
                )

                return ToolResult(
                    result={
                        "status": "success" if equivalent else "failed",
                        **trust_metadata(
                            source="tool",
                            tool="yosys",
                            tool_available=True,
                            command=result.command or command,
                            artifacts=artifacts,
                        ),
                        "equivalent": equivalent,
                        "mismatches": mismatches,
                        "report": report,
                    },
                    issues=[] if equivalent else ["Equivalence not proven"],
                )

            except Exception as e:
                return ToolResult(
                    result={
                        "status": "error",
                        "message": str(e),
                        **trust_metadata(source="tool", tool="yosys", tool_available=True),
                    },
                    issues=[f"Yosys execution error: {e}"],
                )
