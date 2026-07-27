"""Coordinator agent - orchestrates multi-agent workflows."""
from typing import Any, Dict, List
from .base_agent import BaseAgent, AgentResult
from .hardware_agent import HardwareAgent
from .verification_agent import VerificationAgent
from .software_agent import SoftwareAgent
from .shared_memory import SharedMemory


class AgentCoordinator:
    """Coordinator that orchestrates multiple specialized agents."""

    def __init__(self):
        self.shared_memory = SharedMemory()
        self.agents = {
            "hardware": HardwareAgent(self.shared_memory),
            "verification": VerificationAgent(self.shared_memory),
            "software": SoftwareAgent(self.shared_memory),
        }

    def coordinate(self, workflow: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Coordinate a multi-agent workflow.

        Args:
            workflow: List of tasks, each with:
                - agent: str (hardware/verification/software)
                - task_type: str
                - inputs: Dict[str, Any]

        Returns:
            Dict with workflow results
        """
        results = []
        all_artifacts = {}
        all_errors = []

        for step in workflow:
            agent_name = step.get("agent")
            agent = self.agents.get(agent_name)

            if not agent:
                all_errors.append(f"Unknown agent: {agent_name}")
                continue

            # Execute task
            result = agent.execute(step)
            results.append({
                "agent": agent_name,
                "task_type": step.get("task_type"),
                "success": result.success,
                "messages": result.messages,
                "errors": result.errors,
            })

            # Collect artifacts
            all_artifacts.update(result.artifacts)

            # Collect errors
            all_errors.extend(result.errors)

        # Get all stored artifacts
        stored_artifacts = {
            name: artifact.content
            for name, artifact in self.shared_memory.artifacts.items()
        }

        return {
            "success": len(all_errors) == 0,
            "results": results,
            "artifacts": stored_artifacts,
            "errors": all_errors,
            "decisions": self.shared_memory.get_decisions(),
        }

    def execute_hw_sw_codesign(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a complete HW/SW co-design workflow."""
        module_name = task.get("module_name", "dut")

        workflow = [
            # Hardware: generate register definitions
            {
                "agent": "hardware",
                "task_type": "reg_definition",
                "reg_code": task.get("reg_code", ""),
            },
            # Hardware: generate RTL
            {
                "agent": "hardware",
                "task_type": "rtl_generation",
                "rtl_code": task.get("rtl_code", ""),
            },
            # Software: generate register header
            {
                "agent": "software",
                "task_type": "register_header",
                "header_code": task.get("header_code", ""),
            },
            # Software: generate HAL
            {
                "agent": "software",
                "task_type": "hal_library",
                "hal_code": task.get("hal_code", ""),
            },
            # Verification: generate testbench
            {
                "agent": "verification",
                "task_type": "testbench_generation",
                "tb_code": task.get("tb_code", ""),
            },
            # Verification: run simulation
            {
                "agent": "verification",
                "task_type": "simulation",
            },
            # Software: generate driver
            {
                "agent": "software",
                "task_type": "linux_driver",
                "driver_code": task.get("driver_code", ""),
                "module_name": module_name,
            },
            # Hardware: alignment check
            {
                "agent": "hardware",
                "task_type": "alignment_check",
            },
        ]

        return self.coordinate(workflow)
