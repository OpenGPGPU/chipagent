"""DPI co-simulation tool using Verilator."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from chipagent.tools.base import Tool, ToolContext, ToolResult, trust_metadata


class DPICosimTool(Tool):
    """DPI-based co-simulation between C/C++ and Verilog using Verilator."""
    name = "run_dpi_cosim"

    def run(self, ctx: ToolContext) -> ToolResult:
        """Run DPI co-simulation.

        Expected inputs:
            reg_code: Verilog RTL code with DPI imports
            dpi_code: C/C++ DPI implementation code
            testbench: Verilog testbench code
            simulation_cycles: Number of cycles to simulate (default: 100)
        """
        reg_code = ctx.inputs.get("reg_code", "").strip()
        dpi_code = ctx.inputs.get("dpi_code", "").strip()
        testbench = ctx.inputs.get("testbench", "").strip()
        cycles = ctx.inputs.get("simulation_cycles", 100)

        if not reg_code:
            return ToolResult(
                result={"status": "error", "message": "No reg_code provided"},
                issues=["Missing input: reg_code"],
            )

        if not testbench:
            return ToolResult(
                result={"status": "error", "message": "No testbench provided"},
                issues=["Missing input: testbench"],
            )

        testbench_module = self._top_module(testbench)
        if not testbench_module:
            return ToolResult(
                result={"status": "error", "message": "Unable to determine testbench top module"},
                issues=["Invalid input: testbench has no module declaration"],
            )

        # Check for Verilator
        if not shutil.which("verilator"):
            return self._estimate_cosim(reg_code, dpi_code, testbench, cycles)

        return self._run_verilator_dpi(
            reg_code, dpi_code, testbench, cycles, testbench_module
        )

    def _run_verilator_dpi(self, reg_code: str, dpi_code: str,
                           testbench: str, cycles: int,
                           testbench_module: str) -> ToolResult:
        """Run actual Verilator DPI co-simulation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Write RTL code
            rtl_file = tmpdir / "dut.v"
            rtl_file.write_text(reg_code)

            # Write testbench
            tb_file = tmpdir / "tb.v"
            tb_file.write_text(testbench)

            # Write DPI C++ code if provided
            dpi_file = None
            if dpi_code:
                dpi_file = tmpdir / "dpi.cpp"
                dpi_file.write_text(dpi_code)

            # ``--binary`` supplies Verilator's generated simulation main.
            # The previous ``--cc --exe`` invocation required a caller-provided
            # C++ main and incorrectly passed dpi.cpp as a compiler flag.
            verilator_cmd = [
                "verilator",
                "--binary",
                "--timing",
                "--top-module", testbench_module,
                "-Wno-fatal",
                "--Mdir", str(tmpdir / "obj_dir"),
                "-j", "0",
            ]

            verilator_cmd.extend([str(rtl_file), str(tb_file)])
            if dpi_file:
                verilator_cmd.append(str(dpi_file))

            try:
                result = subprocess.run(
                    verilator_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=tmpdir,
                )

                if result.returncode != 0:
                    return ToolResult(
                        result={
                            "status": "error",
                            "compilation": "failed",
                            "stage": "compile",
                            "stderr": result.stderr[-4000:],
                            **trust_metadata(
                                source="tool",
                                tool="verilator",
                                tool_available=True,
                                command=verilator_cmd,
                            ),
                        },
                        issues=["Verilator compilation failed"],
                    )

                # Run simulation
                sim_exe = tmpdir / "obj_dir" / f"V{testbench_module}"
                if not sim_exe.exists():
                    return ToolResult(
                        result={
                            "status": "error",
                            "compilation": "success",
                            "stage": "locate_executable",
                            "message": "Simulation executable not found",
                            **trust_metadata(
                                source="tool",
                                tool="verilator",
                                tool_available=True,
                                command=verilator_cmd,
                            ),
                        },
                        issues=["Simulation executable not generated"],
                    )

                sim_result = subprocess.run(
                    [str(sim_exe)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=tmpdir,
                )

                # Parse simulation output
                sim_output = self._parse_simulation_output(sim_result.stdout, sim_result.stderr)
                failed = (
                    sim_result.returncode != 0
                    or sim_output["assertions_failed"] > 0
                    or bool(re.search(r"(?:FAIL|TEST FAILED|ERROR)", sim_result.stdout, re.IGNORECASE))
                )

                return ToolResult(
                    result={
                        "status": "error" if failed else "success",
                        "compilation": "success",
                        "simulation": sim_output,
                        "cycles": cycles,
                        "stdout": sim_result.stdout,
                        "stderr": sim_result.stderr,
                        "returncode": sim_result.returncode,
                        **trust_metadata(
                            source="tool",
                            tool="verilator",
                            tool_available=True,
                            command=[str(sim_exe)],
                        ),
                    },
                    issues=["Simulation failed"] if failed else [],
                )

            except subprocess.TimeoutExpired:
                return ToolResult(
                    result={
                        "status": "error",
                        "stage": "timeout",
                        **trust_metadata(
                            source="tool",
                            tool="verilator",
                            tool_available=True,
                            command=verilator_cmd,
                        ),
                    },
                    issues=["Verilator DPI co-simulation timed out"],
                )
            except Exception as e:
                return ToolResult(
                    result={"status": "error", "message": str(e)},
                    issues=[f"Verilator execution error: {e}"],
                )

    def _estimate_cosim(self, reg_code: str, dpi_code: str,
                        testbench: str, cycles: int) -> ToolResult:
        """Estimate co-simulation results when Verilator is unavailable."""
        # Analyze DPI imports
        dpi_imports = self._analyze_dpi_imports(reg_code)

        # Analyze DPI exports
        dpi_exports = self._analyze_dpi_exports(dpi_code) if dpi_code else []

        # Estimate testbench coverage
        tb_coverage = self._estimate_testbench_coverage(testbench, cycles)

        return ToolResult(
                result={
                    "status": "estimated",
                    **trust_metadata(
                        source="static_estimate",
                        tool="static_analysis",
                        tool_available=False,
                    ),
                "dpi_imports": dpi_imports,
                "dpi_exports": dpi_exports,
                "testbench_analysis": tb_coverage,
                "cycles": cycles,
                "message": "Verilator not available, using static analysis",
            },
            issues=["Verilator not available, results are estimates"],
        )

    @staticmethod
    def _top_module(testbench: str) -> Optional[str]:
        """Return the first module declared by a testbench."""
        match = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)", testbench)
        return match.group(1) if match else None

    def _analyze_dpi_imports(self, reg_code: str) -> Dict[str, Any]:
        """Analyze DPI import statements in Verilog code."""
        imports = []

        # Match DPI import declarations
        dpi_import_pattern = r'import\s+"DPI-C"\s+(?:function|task)\s+(\w+)\s*(?:\([^)]*\))?\s*;'
        matches = re.finditer(dpi_import_pattern, reg_code, re.MULTILINE)

        for match in matches:
            imports.append({
                "name": match.group(1),
                "line": reg_code[:match.start()].count('\n') + 1,
            })

        return {
            "count": len(imports),
            "functions": imports,
        }

    def _analyze_dpi_exports(self, dpi_code: str) -> Dict[str, Any]:
        """Analyze DPI export functions in C/C++ code."""
        exports = []

        # Match DPI export function declarations
        dpi_export_pattern = r'(?:extern\s+"C"\s+)?(?:DPI_DLLESPEC\s+)?(\w+)\s+(\w+)\s*\([^)]*\)'
        matches = re.finditer(dpi_export_pattern, dpi_code, re.MULTILINE)

        for match in matches:
            exports.append({
                "return_type": match.group(1),
                "name": match.group(2),
                "line": dpi_code[:match.start()].count('\n') + 1,
            })

        return {
            "count": len(exports),
            "functions": exports,
        }

    def _estimate_testbench_coverage(self, testbench: str, cycles: int) -> Dict[str, Any]:
        """Estimate testbench coverage."""
        # Count assertions
        assertions = len(re.findall(r'assert\s*\(', testbench))

        # Count $display/$write statements
        displays = len(re.findall(r'\$(?:display|write|monitor)\s*\(', testbench))

        # Count initial blocks
        initial_blocks = len(re.findall(r'initial\s+begin', testbench))

        # Estimate coverage
        coverage_score = min(100, (assertions * 20) + (displays * 5) + (initial_blocks * 10))

        return {
            "assertions": assertions,
            "displays": displays,
            "initial_blocks": initial_blocks,
            "estimated_coverage": coverage_score,
            "cycles": cycles,
        }

    def _parse_simulation_output(self, stdout: str, stderr: str) -> Dict[str, Any]:
        """Parse Verilator simulation output."""
        output = {
            "passed": False,
            "cycles_run": 0,
            "assertions_passed": 0,
            "assertions_failed": 0,
            "messages": [],
        }

        # Check for pass/fail indicators
        if re.search(r'(?:PASS|TEST PASSED|SUCCESS)', stdout, re.IGNORECASE):
            output["passed"] = True
        elif re.search(r'(?:FAIL|TEST FAILED|ERROR)', stdout, re.IGNORECASE):
            output["passed"] = False

        # Count cycles
        cycle_match = re.search(r'(?:cycle|time)[:\s]+(\d+)', stdout, re.IGNORECASE)
        if cycle_match:
            output["cycles_run"] = int(cycle_match.group(1))

        # Count assertions
        passed_match = re.search(r'(\d+)\s+assertions?\s+passed', stdout, re.IGNORECASE)
        if passed_match:
            output["assertions_passed"] = int(passed_match.group(1))

        failed_match = re.search(r'(\d+)\s+assertions?\s+failed', stdout, re.IGNORECASE)
        if failed_match:
            output["assertions_failed"] = int(failed_match.group(1))

        # Extract key messages
        for line in stdout.split('\n'):
            if any(keyword in line.upper() for keyword in ['ASSERT', 'CHECK', 'RESULT', 'TEST']):
                output["messages"].append(line.strip())

        return output
