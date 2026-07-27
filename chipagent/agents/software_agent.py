"""Software agent - handles driver, HAL, and register header generation."""
from typing import Any, Dict
from .base_agent import BaseAgent, AgentResult
from ..tools.interface import SwHwInterfaceTool
from ..tools.alignment import RegisterAlignmentTool


class SoftwareAgent(BaseAgent):
    """Agent specialized in software development tasks."""

    def __init__(self, shared_memory=None):
        super().__init__("software", "driver, HAL, register headers, BSP", shared_memory)
        self.interface_tool = SwHwInterfaceTool()
        self.align_tool = RegisterAlignmentTool()

    def execute(self, task: Dict[str, Any]) -> AgentResult:
        """Execute software development task."""
        task_type = task.get("task_type")
        artifacts = {}
        messages = []
        errors = []

        if task_type == "linux_driver":
            # Generate Linux driver
            driver_code = task.get("driver_code")
            if driver_code:
                self.store_artifact("driver_code", driver_code)
                messages.append("Linux driver generated and stored")

                # Check interface with RTL
                reg_code = task.get("reg_code") or self.get_artifact("reg_code")
                if reg_code:
                    ctx = self._make_context({
                        "reg_code": reg_code,
                        "driver_code": driver_code,
                        "module_name": task.get("module_name", "dut"),
                    })
                    result = self.interface_tool.run(ctx)
                    artifacts["interface_check"] = result.result

                    if result.result.get("aligned"):
                        messages.append("Driver-RTL interface aligned")
                    else:
                        errors.append(f"Interface mismatch: {result.result}")

        elif task_type == "hal_library":
            # Generate HAL library
            hal_code = task.get("hal_code")
            if hal_code:
                self.store_artifact("hal_code", hal_code)
                messages.append("HAL library generated and stored")

        elif task_type == "register_header":
            # Generate register header
            header_code = task.get("header_code")
            if header_code:
                self.store_artifact("header_code", header_code)
                messages.append("Register header generated and stored")

                # Check alignment with RTL
                reg_code = task.get("reg_code") or self.get_artifact("reg_code")
                if reg_code:
                    ctx = self._make_context({
                        "reg_code": reg_code,
                        "header_code": header_code,
                    })
                    result = self.align_tool.run(ctx)
                    artifacts["alignment_check"] = result.result

                    if result.result.get("aligned"):
                        messages.append("Register alignment passed")
                    else:
                        errors.append(f"Alignment mismatch: {result.result}")

        elif task_type == "bsp_generation":
            # Generate BSP
            bsp_code = task.get("bsp_code")
            if bsp_code:
                self.store_artifact("bsp_code", bsp_code)
                messages.append("BSP generated and stored")

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
