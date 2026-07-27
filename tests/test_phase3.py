"""Tests for Phase 3 tools: Synthesis, Physical Design, Knowledge, Agents, Registry, Security, DPI."""
import json
import pytest
from pathlib import Path


class TestSynthesisTools:
    """Test synthesis MCP tools."""

    def test_run_synthesis_basic(self):
        """Test basic synthesis with simple RTL."""
        from chipagent.mcp import chipagent_run_synthesis

        rtl_code = """
        module adder(
            input [7:0] a,
            input [7:0] b,
            output [8:0] sum
        );
            assign sum = a + b;
        endmodule
        """

        result = chipagent_run_synthesis(
            reg_code=rtl_code,
            module_name="adder"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if Yosys is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "cells" in data
            assert "netlist" in data
        else:
            # Tool not available - should provide helpful error message
            assert "message" in data
            assert "Yosys" in data["message"]

    def test_run_synthesis_empty_input(self):
        """Test synthesis with empty input."""
        from chipagent.mcp import chipagent_run_synthesis

        result = chipagent_run_synthesis(reg_code="", module_name="empty")
        data = json.loads(result)

        assert data["status"] == "error"

    def test_analyze_timing_basic(self):
        """Test timing analysis."""
        from chipagent.mcp import chipagent_analyze_timing

        netlist = """
        module counter(
            input clk,
            input rst,
            output reg [7:0] count
        );
            always @(posedge clk or posedge rst) begin
                if (rst) count <= 0;
                else count <= count + 1;
            end
        endmodule
        """

        result = chipagent_analyze_timing(
            netlist=netlist,
            module_name="counter"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if OpenSTA/Yosys is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "slack" in data
            assert "critical_path" in data
        else:
            assert "message" in data

    def test_optimize_area_basic(self):
        """Test area optimization."""
        from chipagent.mcp import chipagent_optimize_area

        netlist = """
        module mux(
            input sel,
            input [7:0] a,
            input [7:0] b,
            output [7:0] out
        );
            assign out = sel ? a : b;
        endmodule
        """

        result = chipagent_optimize_area(
            netlist=netlist,
            area_constraint=100.0,
            module_name="mux"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if Yosys is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "optimized_netlist" in data
            assert "area_before" in data
            assert "area_after" in data
        else:
            assert "message" in data
            assert "Yosys" in data["message"]

    def test_analyze_power_basic(self):
        """Test power analysis."""
        from chipagent.mcp import chipagent_analyze_power

        netlist = """
        module reg_file(
            input clk,
            input [4:0] addr,
            input [7:0] data_in,
            output [7:0] data_out
        );
            reg [7:0] regs [0:31];
            always @(posedge clk) begin
                regs[addr] <= data_in;
            end
            assign data_out = regs[addr];
        endmodule
        """

        result = chipagent_analyze_power(
            netlist=netlist,
            module_name="reg_file"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if Yosys is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "dynamic_power" in data
            assert "leakage_power" in data
            assert "total_power" in data
        else:
            assert "message" in data
            assert "Yosys" in data["message"]

    def test_run_formality_basic(self):
        """Test formal verification."""
        from chipagent.mcp import chipagent_run_formality

        reference = """
        module adder(
            input [7:0] a,
            input [7:0] b,
            output [8:0] sum
        );
            assign sum = a + b;
        endmodule
        """

        implementation = """
        module adder(
            input [7:0] a,
            input [7:0] b,
            output [8:0] sum
        );
            wire [8:0] temp;
            assign temp = a + b;
            assign sum = temp;
        endmodule
        """

        result = chipagent_run_formality(
            reference_netlist=reference,
            implementation_netlist=implementation,
            module_name="adder"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if Yosys is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "equivalent" in data
            assert data["equivalent"] is True
        else:
            assert "message" in data
            assert "Yosys" in data["message"]


class TestPhysicalDesignTools:
    """Test physical design MCP tools."""

    def test_create_floorplan_basic(self):
        """Test floorplan creation."""
        from chipagent.mcp import chipagent_create_floorplan

        netlist = """
        module top(
            input clk,
            input [7:0] data_in,
            output [7:0] data_out
        );
            // Simple passthrough
            assign data_out = data_in;
        endmodule
        """

        result = chipagent_create_floorplan(
            netlist=netlist,
            constraints={"utilization": 0.7, "aspect_ratio": 1.0},
            module_name="top"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if OpenROAD is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "die_area" in data
            assert "core_area" in data
            assert "utilization" in data
        else:
            # Tool not available - should provide helpful error message
            assert "message" in data
            assert "OpenROAD" in data["message"]

    def test_run_placement_basic(self):
        """Test cell placement."""
        from chipagent.mcp import chipagent_run_placement

        netlist = """
        module top(
            input clk,
            input [7:0] data_in,
            output [7:0] data_out
        );
            assign data_out = data_in;
        endmodule
        """

        floorplan = {"die_area": 10000, "core_area": 7000, "utilization": 0.7}

        result = chipagent_run_placement(
            netlist=netlist,
            floorplan=floorplan,
            module_name="top"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if OpenROAD is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "placed_cells" in data
        else:
            assert "message" in data
            assert "OpenROAD" in data["message"]

    def test_run_cts_basic(self):
        """Test clock tree synthesis."""
        from chipagent.mcp import chipagent_run_cts

        netlist = """
        module top(
            input clk,
            input [7:0] data_in,
            output reg [7:0] data_out
        );
            always @(posedge clk) begin
                data_out <= data_in;
            end
        endmodule
        """

        placement = {"placement_report": "placed"}

        result = chipagent_run_cts(
            netlist=netlist,
            placement=placement,
            module_name="top"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if OpenROAD is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "skew" in data
            assert "insertion_delay" in data
        else:
            assert "message" in data
            assert "OpenROAD" in data["message"]

    def test_run_routing_basic(self):
        """Test signal routing."""
        from chipagent.mcp import chipagent_run_routing

        netlist = """
        module top(
            input clk,
            input [7:0] data_in,
            output reg [7:0] data_out
        );
            always @(posedge clk) begin
                data_out <= data_in;
            end
        endmodule
        """

        cts = {"skew": 0.1, "insertion_delay": 0.2}

        result = chipagent_run_routing(
            netlist=netlist,
            cts=cts,
            module_name="top"
        )
        data = json.loads(result)

        # After refactoring: expect "success" if OpenROAD is installed, "error" if not
        assert data["status"] in ["success", "error"]
        if data["status"] == "success":
            assert "wirelength" in data
            assert "congestion" in data
        else:
            assert "message" in data
            assert "OpenROAD" in data["message"]

    def test_run_drc_check_basic(self):
        """Test DRC check."""
        from chipagent.mcp import chipagent_run_drc_check

        netlist = """
        module top(
            input clk,
            output [7:0] data_out
        );
            assign data_out = 8'hFF;
        endmodule
        """

        result = chipagent_run_drc_check(
            netlist=netlist,
            module_name="top"
        )
        data = json.loads(result)

        # After refactoring: expect "passed"/"failed" if Magic is installed, "error" if not
        assert data["status"] in ["passed", "failed", "error"]
        if data["status"] in ["passed", "failed"]:
            assert "violations" in data
        else:
            assert "message" in data
            assert "Magic" in data["message"]

    def test_run_lvs_check_basic(self):
        """Test LVS check."""
        from chipagent.mcp import chipagent_run_lvs_check

        layout = """
        module top(
            input clk,
            output [7:0] data_out
        );
            assign data_out = 8'hFF;
        endmodule
        """

        netlist = """
        module top(
            input clk,
            output [7:0] data_out
        );
            assign data_out = 8'hFF;
        endmodule
        """

        result = chipagent_run_lvs_check(
            layout=layout,
            netlist=netlist,
            module_name="top"
        )
        data = json.loads(result)

        # After refactoring: expect "success"/"passed"/"failed" if Netgen is installed, "error" if not
        assert data["status"] in ["success", "passed", "failed", "error"]
        if data["status"] in ["success", "passed", "failed"]:
            assert "match" in data
        else:
            assert "message" in data
            assert "Netgen" in data["message"]


class TestKnowledgeTools:
    """Test knowledge base MCP tools."""

    def test_query_knowledge_base(self):
        """Test knowledge base query."""
        from chipagent.mcp import chipagent_query_knowledge_base

        result = chipagent_query_knowledge_base(
            query="AXI protocol specification",
            top_k=3
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_search_code_examples(self):
        """Test code example search."""
        from chipagent.mcp import chipagent_search_code_examples

        result = chipagent_search_code_examples(
            description="FIFO implementation",
            top_k=3
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert "examples" in data

    def test_consult_architecture(self):
        """Test architecture consultation."""
        from chipagent.mcp import chipagent_consult_architecture

        result = chipagent_consult_architecture(
            requirement="Design a pipelined processor",
            top_k=3
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert "consultation" in data

    def test_diagnose_issue(self):
        """Test issue diagnosis."""
        from chipagent.mcp import chipagent_diagnose_issue

        result = chipagent_diagnose_issue(
            error_message="Timing violation in critical path",
            top_k=3
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert "diagnosis" in data

    def test_generate_documentation(self):
        """Test documentation generation."""
        from chipagent.mcp import chipagent_generate_documentation

        result = chipagent_generate_documentation(
            module_name="adder",
            design_info="8-bit adder with carry",
            top_k=3
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert "documentation" in data

    def test_search_software_reference(self):
        """Test software reference search."""
        from chipagent.mcp import chipagent_search_software_reference

        result = chipagent_search_software_reference(
            query="register read function",
            top_k=3
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert "references" in data

    def test_consult_sw_hw_co_design(self):
        """Test SW/HW co-design consultation."""
        from chipagent.mcp import chipagent_consult_sw_hw_co_design

        result = chipagent_consult_sw_hw_co_design(
            requirement="Design driver for custom peripheral",
            top_k=3
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert "consultation" in data


class TestMultiAgentTools:
    """Test multi-agent coordination tools."""

    def test_coordinate_hw_sw_codesign(self):
        """Test HW/SW co-design coordination."""
        from chipagent.mcp import chipagent_coordinate_hw_sw_codesign

        result = chipagent_coordinate_hw_sw_codesign(
            module_name="uart",
            reg_code="module uart_reg(input clk, output [7:0] data); endmodule",
            rtl_code="module uart(input clk, output [7:0] data); endmodule",
            header_code="// uart_regs.h",
            hal_code="// uart_hal.c",
            tb_code="module uart_tb; endmodule",
            driver_code="// uart_driver.c"
        )
        data = json.loads(result)

        assert "success" in data
        assert "results" in data
        assert "artifacts" in data

    def test_execute_agent_workflow(self):
        """Test custom agent workflow execution."""
        from chipagent.mcp import chipagent_execute_agent_workflow

        workflow = [
            {
                "agent": "hardware",
                "task_type": "rtl_generation",
                "inputs": {"request": "Generate adder"}
            },
            {
                "agent": "verification",
                "task_type": "testbench_generation",
                "inputs": {"request": "Generate testbench"}
            }
        ]

        result = chipagent_execute_agent_workflow(workflow=workflow)
        data = json.loads(result)

        assert "success" in data
        assert "results" in data


class TestRegistryTools:
    """Test service registry tools."""

    def test_list_available_tools(self):
        """Test listing available tools."""
        from chipagent.mcp import chipagent_list_available_tools

        result = chipagent_list_available_tools(category=None)
        data = json.loads(result)

        assert data["status"] == "success"
        assert "tools" in data
        assert data["count"] > 0

    def test_list_available_tools_by_category(self):
        """Test listing tools by category."""
        from chipagent.mcp import chipagent_list_available_tools

        result = chipagent_list_available_tools(category="synthesis")
        data = json.loads(result)

        assert data["status"] == "success"
        assert "tools" in data
        # Should have synthesis tools
        assert any(t.get("category") == "synthesis" for t in data["tools"])

    def test_discover_services(self):
        """Test service discovery."""
        from chipagent.mcp import chipagent_discover_services

        result = chipagent_discover_services(capability="synthesis")
        data = json.loads(result)

        assert data["status"] == "success"
        assert "services" in data


class TestSecurityTools:
    """Test security tools."""

    def test_auth_status_disabled(self):
        """Test auth status when authentication is disabled."""
        from chipagent.mcp import chipagent_auth_status

        result = chipagent_auth_status()
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["authenticated"] is False
        assert "disabled" in data["message"].lower()

    def test_check_permission_disabled(self):
        """Test permission check when authentication is disabled."""
        from chipagent.mcp import chipagent_check_permission

        result = chipagent_check_permission(tool_name="chipagent_run_synthesis")
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["authorized"] is True


class TestDPICosim:
    """Test DPI co-simulation tool."""

    def test_run_dpi_cosim_basic(self):
        """Test basic DPI co-simulation."""
        from chipagent.mcp import chipagent_run_dpi_cosim

        reg_code = """
        module dpi_test(
            input clk,
            input [7:0] data_in,
            output [7:0] data_out
        );
            // DPI import
            import "DPI-C" function void process_data(input byte unsigned data);

            always @(posedge clk) begin
                process_data(data_in);
            end

            assign data_out = data_in;
        endmodule
        """

        dpi_code = """
        #include <stdio.h>

        extern "C" void process_data(unsigned char data) {
            printf("Processing: %d\\n", data);
        }
        """

        testbench = """
        module dpi_test_tb;
            reg clk;
            reg [7:0] data_in;
            wire [7:0] data_out;

            dpi_test dut(.clk(clk), .data_in(data_in), .data_out(data_out));

            initial begin
                clk = 0;
                forever #5 clk = ~clk;
            end

            initial begin
                data_in = 0;
                #100;
                data_in = 42;
                #100;
                $finish;
            end
        endmodule
        """

        result = chipagent_run_dpi_cosim(
            reg_code=reg_code,
            dpi_code=dpi_code,
            testbench=testbench,
            simulation_cycles=10,
            module_name="dpi_test"
        )
        data = json.loads(result)

        assert data["status"] in ["success", "estimated", "error"]
        # If verilator not available, should be estimated
        if data["status"] == "estimated":
            assert "dpi_imports" in data
            assert "dpi_exports" in data
