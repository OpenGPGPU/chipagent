"""Hardware agent - handles RTL generation, verification, and elaboration."""
from typing import Any, Dict
from .base_agent import BaseAgent, AgentResult
from ..tools.simulation import RunSimulationTool
from ..tools.elaborate import ElaborateCheckTool
from ..tools.alignment import RegisterAlignmentTool
from ..tools.interface import SwHwInterfaceTool


class HardwareAgent(BaseAgent):
    """Agent specialized in hardware design tasks."""

    def __init__(self, shared_memory=None):
        super().__init__("hardware", "RTL generation, verification, elaboration", shared_memory)
        self.sim_tool = RunSimulationTool()
        self.elaborate_tool = ElaborateCheckTool()
        self.align_tool = RegisterAlignmentTool()
        self.interface_tool = SwHwInterfaceTool()

    def execute(self, task: Dict[str, Any]) -> AgentResult:
        """Execute hardware design task."""
        task_type = task.get("task_type")
        artifacts = {}
        messages = []
        errors = []

        if task_type == "rtl_generation":
            # Generate RTL
            rtl_code = task.get("rtl_code")
            if rtl_code:
                self.store_artifact("rtl_code", rtl_code)
                messages.append("RTL code generated and stored")

                # Run elaboration
                ctx = self._make_context({"reg_code": rtl_code})
                result = self.elaborate_tool.run(ctx)
                if result.result.get("status") == "passed":
                    artifacts["elaboration"] = result.result
                    messages.append("Elaboration passed")
                else:
                    errors.append(f"Elaboration failed: {result.result}")

        elif task_type == "reg_definition":
            # Generate register definitions
            reg_code = task.get("reg_code")
            if reg_code:
                self.store_artifact("reg_code", reg_code)
                messages.append("Register definitions generated and stored")

        elif task_type == "verification":
            # Run simulation
            reg_code = task.get("reg_code") or self.get_artifact("reg_code")
            tb_code = task.get("tb_code") or self.get_artifact("tb_code")

            if reg_code and tb_code:
                ctx = self._make_context({"reg_code": reg_code, "tb_code": tb_code})
                result = self.sim_tool.run(ctx)
                artifacts["simulation"] = result.result

                if result.result.get("passed") == "passed":
                    messages.append("Simulation passed")
                else:
                    errors.append(f"Simulation failed: {result.result}")

        elif task_type == "alignment_check":
            # Check alignment
            reg_code = task.get("reg_code") or self.get_artifact("reg_code")
            header_code = task.get("header_code") or self.get_artifact("header_code")

            if reg_code and header_code:
                ctx = self._make_context({"reg_code": reg_code, "header_code": header_code})
                result = self.align_tool.run(ctx)
                artifacts["alignment"] = result.result

                if result.result.get("aligned"):
                    messages.append("Alignment check passed")
                else:
                    errors.append(f"Alignment mismatch: {result.result}")

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
