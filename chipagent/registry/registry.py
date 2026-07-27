"""Service registry for managing tool availability and metadata."""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ToolMetadata:
    """Metadata for a tool."""
    name: str
    category: str
    description: str
    version: str = "1.0.0"
    requires_eda: bool = False
    eda_tools: List[str] = field(default_factory=list)
    supported_formats: List[str] = field(default_factory=list)
    estimated_runtime: str = "fast"  # fast, medium, slow


@dataclass
class ServiceRegistration:
    """Service registration entry."""
    service_id: str
    name: str
    tools: List[ToolMetadata]
    status: str = "active"  # active, inactive, maintenance
    registered_at: datetime = field(default_factory=datetime.now)
    last_heartbeat: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ServiceRegistry:
    """Registry for managing available tools and services."""

    def __init__(self):
        self.services: Dict[str, ServiceRegistration] = {}
        self.tools: Dict[str, ToolMetadata] = {}
        self._init_default_tools()

    def _init_default_tools(self):
        """Initialize default tool registrations."""
        # Synthesis tools
        self.register_tool(ToolMetadata(
            name="run_synthesis",
            category="synthesis",
            description="Run full synthesis flow",
            requires_eda=True,
            eda_tools=["yosys"],
            supported_formats=["verilog", "systemverilog"],
            estimated_runtime="medium",
        ))

        self.register_tool(ToolMetadata(
            name="analyze_timing",
            category="synthesis",
            description="Analyze timing after synthesis",
            requires_eda=True,
            eda_tools=["yosys", "opensta"],
            supported_formats=["verilog"],
            estimated_runtime="medium",
        ))

        self.register_tool(ToolMetadata(
            name="optimize_area",
            category="synthesis",
            description="Optimize for area",
            requires_eda=True,
            eda_tools=["yosys"],
            supported_formats=["verilog"],
            estimated_runtime="medium",
        ))

        self.register_tool(ToolMetadata(
            name="analyze_power",
            category="synthesis",
            description="Analyze power consumption",
            requires_eda=True,
            eda_tools=["yosys"],
            supported_formats=["verilog"],
            estimated_runtime="medium",
        ))

        self.register_tool(ToolMetadata(
            name="run_formality",
            category="synthesis",
            description="Run formal verification",
            requires_eda=True,
            eda_tools=["yosys"],
            supported_formats=["verilog"],
            estimated_runtime="slow",
        ))

        # Physical design tools
        self.register_tool(ToolMetadata(
            name="create_floorplan",
            category="physical",
            description="Create floorplan",
            requires_eda=True,
            eda_tools=["openroad"],
            supported_formats=["verilog", "lef"],
            estimated_runtime="medium",
        ))

        self.register_tool(ToolMetadata(
            name="run_placement",
            category="physical",
            description="Run placement",
            requires_eda=True,
            eda_tools=["openroad"],
            supported_formats=["verilog"],
            estimated_runtime="slow",
        ))

        self.register_tool(ToolMetadata(
            name="run_cts",
            category="physical",
            description="Run clock tree synthesis",
            requires_eda=True,
            eda_tools=["openroad"],
            supported_formats=["verilog"],
            estimated_runtime="slow",
        ))

        self.register_tool(ToolMetadata(
            name="run_routing",
            category="physical",
            description="Run routing",
            requires_eda=True,
            eda_tools=["openroad"],
            supported_formats=["verilog"],
            estimated_runtime="slow",
        ))

        self.register_tool(ToolMetadata(
            name="run_drc_check",
            category="physical",
            description="Run DRC check",
            requires_eda=True,
            eda_tools=["magic"],
            supported_formats=["gds", "def"],
            estimated_runtime="medium",
        ))

        self.register_tool(ToolMetadata(
            name="run_lvs_check",
            category="physical",
            description="Run LVS check",
            requires_eda=True,
            eda_tools=["netgen"],
            supported_formats=["spice", "verilog"],
            estimated_runtime="medium",
        ))

        # Existing tools
        self.register_tool(ToolMetadata(
            name="run_simulation",
            category="verification",
            description="Run simulation",
            requires_eda=True,
            eda_tools=["iverilog", "verilator"],
            supported_formats=["verilog", "systemverilog"],
            estimated_runtime="medium",
        ))

        self.register_tool(ToolMetadata(
            name="check_register_alignment",
            category="verification",
            description="Check register alignment",
            requires_eda=False,
            supported_formats=["verilog", "c"],
            estimated_runtime="fast",
        ))

        self.register_tool(ToolMetadata(
            name="check_sw_hw_interface",
            category="verification",
            description="Check SW/HW interface",
            requires_eda=False,
            supported_formats=["verilog", "c"],
            estimated_runtime="fast",
        ))

        self.register_tool(ToolMetadata(
            name="analyze_coverage",
            category="verification",
            description="Analyze coverage",
            requires_eda=False,
            supported_formats=["json"],
            estimated_runtime="fast",
        ))

        self.register_tool(ToolMetadata(
            name="run_sw_hw_cosim",
            category="verification",
            description="Run SW/HW co-simulation",
            requires_eda=False,
            supported_formats=["verilog", "c"],
            estimated_runtime="medium",
        ))

        self.register_tool(ToolMetadata(
            name="elaborate",
            category="synthesis",
            description="Elaborate design",
            requires_eda=True,
            eda_tools=["yosys"],
            supported_formats=["verilog", "systemverilog"],
            estimated_runtime="fast",
        ))

    def register_tool(self, tool: ToolMetadata):
        """Register a tool."""
        self.tools[tool.name] = tool

    def register_service(self, service: ServiceRegistration):
        """Register a service."""
        self.services[service.service_id] = service

    def get_tool(self, name: str) -> Optional[ToolMetadata]:
        """Get tool metadata by name."""
        return self.tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> List[ToolMetadata]:
        """List all tools, optionally filtered by category."""
        tools = list(self.tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return tools

    def list_services(self) -> List[ServiceRegistration]:
        """List all registered services."""
        return list(self.services.values())

    def update_heartbeat(self, service_id: str):
        """Update service heartbeat."""
        if service_id in self.services:
            self.services[service_id].last_heartbeat = datetime.now()
