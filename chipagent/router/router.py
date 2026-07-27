"""Task router for directing requests to appropriate tools."""
from typing import Dict, Any, List, Optional
from ..registry import ServiceRegistry, ServiceDiscovery
from ..tools.base import ToolContext
from ..models import TaskObject


class TaskRouter:
    """Routes tasks to appropriate tools based on task type and requirements."""

    def __init__(self, registry: Optional[ServiceRegistry] = None):
        self.registry = registry or ServiceRegistry()
        self.discovery = ServiceDiscovery(self.registry)

        # Tool instances cache
        self._tool_cache = {}

    def route_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Route a task to the appropriate tool and execute it.

        Args:
            task: Task dictionary with task_type and inputs

        Returns:
            Result from tool execution
        """
        task_type = task.get("task_type")
        if not task_type:
            return {"status": "error", "message": "No task_type specified"}

        # Get tool metadata
        tool_meta = self.registry.get_tool(task_type)
        if not tool_meta:
            return {"status": "error", "message": f"Unknown task type: {task_type}"}

        # Get or create tool instance
        tool = self._get_tool_instance(task_type)
        if not tool:
            return {"status": "error", "message": f"Tool not available: {task_type}"}

        # Create context
        ctx = ToolContext(
            task=TaskObject(
                task_type=task_type,
                module_name=task.get("module_name", "dut"),
                description=task.get("description", ""),
            ),
            inputs=task.get("inputs", {}),
        )

        # Execute tool
        try:
            result = tool.run(ctx)
            return {
                "status": "success",
                "task_type": task_type,
                "tool": tool_meta.name,
                "category": tool_meta.category,
                "result": result.result,
                "issues": result.issues,
            }
        except Exception as e:
            return {
                "status": "error",
                "task_type": task_type,
                "tool": tool_meta.name,
                "message": str(e),
            }

    def route_workflow(self, workflow: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Route a sequence of tasks.

        Args:
            workflow: List of task dictionaries

        Returns:
            Aggregated results from all tasks
        """
        results = []
        shared_data = {}

        for task in workflow:
            # Merge shared data into task inputs
            task_inputs = task.get("inputs", {})
            task_inputs.update(shared_data)
            task["inputs"] = task_inputs

            # Execute task
            result = self.route_task(task)
            results.append(result)

            # If successful, extract outputs for next tasks
            if result.get("status") == "success":
                if "result" in result:
                    shared_data.update(result["result"])

        return {
            "status": "success" if all(r.get("status") == "success" for r in results) else "partial",
            "results": results,
            "shared_data": shared_data,
        }

    def _get_tool_instance(self, tool_name: str):
        """Get or create a tool instance."""
        if tool_name in self._tool_cache:
            return self._tool_cache[tool_name]

        # Import tool based on name
        tool = None
        try:
            if tool_name == "run_synthesis":
                from ..tools.synth_run import SynthesisTool
                tool = SynthesisTool()
            elif tool_name == "analyze_timing":
                from ..tools.synth_timing import TimingAnalysisTool
                tool = TimingAnalysisTool()
            elif tool_name == "optimize_area":
                from ..tools.synth_area import AreaOptimizeTool
                tool = AreaOptimizeTool()
            elif tool_name == "analyze_power":
                from ..tools.synth_power import PowerAnalysisTool
                tool = PowerAnalysisTool()
            elif tool_name == "run_formality":
                from ..tools.synth_formality import FormalVerifyTool
                tool = FormalVerifyTool()
            elif tool_name == "create_floorplan":
                from ..tools.phys_floorplan import FloorplanTool
                tool = FloorplanTool()
            elif tool_name == "run_placement":
                from ..tools.phys_placement import PlacementTool
                tool = PlacementTool()
            elif tool_name == "run_cts":
                from ..tools.phys_cts import CTSTool
                tool = CTSTool()
            elif tool_name == "run_routing":
                from ..tools.phys_routing import RoutingTool
                tool = RoutingTool()
            elif tool_name == "run_drc_check":
                from ..tools.phys_drc import DRCCheckTool
                tool = DRCCheckTool()
            elif tool_name == "run_lvs_check":
                from ..tools.phys_lvs import LVSCheckTool
                tool = LVSCheckTool()
            elif tool_name == "run_simulation":
                from ..tools.simulation import RunSimulationTool
                tool = RunSimulationTool()
            elif tool_name == "check_register_alignment":
                from ..tools.alignment import RegisterAlignmentTool
                tool = RegisterAlignmentTool()
            elif tool_name == "check_sw_hw_interface":
                from ..tools.interface import SwHwInterfaceTool
                tool = SwHwInterfaceTool()
            elif tool_name == "analyze_coverage":
                from ..tools.coverage import AnalyzeCoverageTool
                tool = AnalyzeCoverageTool()
            elif tool_name == "run_sw_hw_cosim":
                from ..tools.cosim import SwHwCosimTool
                tool = SwHwCosimTool()
            elif tool_name == "elaborate":
                from ..tools.elaborate import ElaborateCheckTool
                tool = ElaborateCheckTool()
        except ImportError:
            pass

        if tool:
            self._tool_cache[tool_name] = tool

        return tool
