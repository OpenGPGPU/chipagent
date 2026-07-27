"""Service discovery for finding appropriate tools."""
from typing import List, Optional
from .registry import ServiceRegistry, ToolMetadata


class ServiceDiscovery:
    """Discover and select appropriate tools for tasks."""

    def __init__(self, registry: ServiceRegistry):
        self.registry = registry

    def find_tools_by_category(self, category: str) -> List[ToolMetadata]:
        """Find all tools in a category."""
        return self.registry.list_tools(category=category)

    def find_tools_by_format(self, format: str) -> List[ToolMetadata]:
        """Find tools that support a specific format."""
        all_tools = self.registry.list_tools()
        return [t for t in all_tools if format in t.supported_formats]

    def find_tools_by_eda(self, eda_tool: str) -> List[ToolMetadata]:
        """Find tools that require a specific EDA tool."""
        all_tools = self.registry.list_tools()
        return [t for t in all_tools if eda_tool in t.eda_tools]

    def find_fast_tools(self) -> List[ToolMetadata]:
        """Find tools with fast runtime."""
        all_tools = self.registry.list_tools()
        return [t for t in all_tools if t.estimated_runtime == "fast"]

    def find_tools_without_eda(self) -> List[ToolMetadata]:
        """Find tools that don't require EDA tools."""
        all_tools = self.registry.list_tools()
        return [t for t in all_tools if not t.requires_eda]

    def get_tool_chain(self, task_types: List[str]) -> List[ToolMetadata]:
        """Get a chain of tools for a sequence of task types."""
        chain = []
        for task_type in task_types:
            tool = self.registry.get_tool(task_type)
            if tool:
                chain.append(tool)
        return chain

    def recommend_tools_for_task(self, task_description: str) -> List[ToolMetadata]:
        """Recommend tools based on task description (simple keyword matching)."""
        recommendations = []

        # Keywords to tool categories mapping
        keyword_map = {
            "synthesis": ["synth", "compile", "optimize"],
            "verification": ["sim", "test", "verify", "coverage", "drc", "lvs"],
            "physical": ["floorplan", "place", "route", "cts", "layout"],
        }

        desc_lower = task_description.lower()

        for category, keywords in keyword_map.items():
            if any(kw in desc_lower for kw in keywords):
                recommendations.extend(self.find_tools_by_category(category))

        # Remove duplicates
        seen = set()
        unique_recommendations = []
        for tool in recommendations:
            if tool.name not in seen:
                seen.add(tool.name)
                unique_recommendations.append(tool)

        return unique_recommendations
