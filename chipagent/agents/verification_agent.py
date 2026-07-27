"""Verification agent - handles testbench, UVM, and simulation."""
from typing import Any, Dict
from .base_agent import BaseAgent, AgentResult
from ..tools.simulation import RunSimulationTool
from ..tools.coverage import AnalyzeCoverageTool
from ..tools.cosim import SwHwCosimTool


class VerificationAgent(BaseAgent):
    """Agent specialized in verification tasks."""

    def __init__(self, shared_memory=None):
        super().__init__("verification", "testbench, UVM, simulation, coverage", shared_memory)
        self.sim_tool = RunSimulationTool()
        self.coverage_tool = AnalyzeCoverageTool()
        self.cosim_tool = SwHwCosimTool()

    def execute(self, task: Dict[str, Any]) -> AgentResult:
        """Execute verification task."""
        task_type = task.get("task_type")
        artifacts = {}
        messages = []
        errors = []

        if task_type == "testbench_generation":
            # Generate testbench
            tb_code = task.get("tb_code")
            if tb_code:
                self.store_artifact("tb_code", tb_code)
                messages.append("Testbench generated and stored")

        elif task_type == "uvm_skeleton":
            # Generate UVM skeleton
            uvm_code = task.get("uvm_code")
            if uvm_code:
                self.store_artifact("uvm_code", uvm_code)
                messages.append("UVM skeleton generated and stored")

        elif task_type == "simulation":
            # Run simulation
            reg_code = task.get("reg_code") or self.get_artifact("reg_code")
            tb_code = task.get("tb_code") or self.get_artifact("tb_code")

            if reg_code and tb_code:
                ctx = self._make_context({"reg_code": reg_code, "tb_code": tb_code})
                result = self.sim_tool.run(ctx)
                artifacts["simulation"] = result.result

                if result.result.get("passed") == "passed":
                    messages.append("Simulation passed")

                    # Analyze coverage
                    cov_ctx = self._make_context({"sim_result": result.result})
                    cov_result = self.coverage_tool.run(cov_ctx)
                    artifacts["coverage"] = cov_result.result
                    messages.append(f"Coverage: {cov_result.result}")
                else:
                    errors.append(f"Simulation failed: {result.result}")

        elif task_type == "cosimulation":
            # Run co-simulation
            reg_code = task.get("reg_code") or self.get_artifact("reg_code")
            driver_code = task.get("driver_code") or self.get_artifact("driver_code")

            if reg_code and driver_code:
                ctx = self._make_context({
                    "reg_code": reg_code,
                    "driver_code": driver_code,
                    "test_scenario": task.get("test_scenario", ""),
                })
                result = self.cosim_tool.run(ctx)
                artifacts["cosimulation"] = result.result

                if result.result.get("status") == "stub":
                    messages.append("Co-simulation stub executed (DPI not yet connected)")
                else:
                    messages.append(f"Co-simulation: {result.result}")

        self.record_decision(
            f"execute_{task_type}",
            f"Executed {task_type} task",
            task_type=task_type,
            artifacts=list(artifacts.keys()),
        )

        return AgentResult(
            success=len(errors) == 0,
            artifacts=artifacts,
            messages=messages,
            errors=errors,
        )

    def _make_context(self, inputs: Dict[str, Any]):
        """Create ToolContext for tool execution."""
        from ..tools.base import ToolContext
        from ..models import TaskObject

        return ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name="dut", description=""),
            inputs=inputs,
        )
