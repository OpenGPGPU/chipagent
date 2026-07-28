"""
End-to-end demonstration of PPA analysis tools.

This test demonstrates the complete PPA analysis workflow:
1. Generate or load RTL code
2. Estimate area, performance, and power
3. Check against PPA targets
4. Get optimization suggestions
"""
import json
import pytest
from pathlib import Path


class TestPPAEndToEnd:
    """End-to-end test for PPA analysis tools."""

    @pytest.fixture
    def simple_rtl(self):
        """Simple RTL code for testing."""
        return """
module simple_counter (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        enable,
    output logic [7:0]  count
);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 8'd0;
        else if (enable)
            count <= count + 8'd1;
    end

endmodule
"""

    @pytest.fixture
    def complex_rtl(self):
        """More complex RTL code for testing."""
        return """
module riscv_alu (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] op_a,
    input  logic [31:0] op_b,
    input  logic [3:0]  alu_op,
    output logic [31:0] result,
    output logic        zero_flag
);

    logic [31:0] alu_result;

    always_comb begin
        case (alu_op)
            4'b0000: alu_result = op_a + op_b;           // ADD
            4'b0001: alu_result = op_a - op_b;           // SUB
            4'b0010: alu_result = op_a & op_b;           // AND
            4'b0011: alu_result = op_a | op_b;           // OR
            4'b0100: alu_result = op_a ^ op_b;           // XOR
            4'b0101: alu_result = op_a << op_b[4:0];     // SLL
            4'b0110: alu_result = op_a >> op_b[4:0];     // SRL
            4'b0111: alu_result = $signed(op_a) >>> op_b[4:0]; // SRA
            4'b1000: alu_result = {31'd0, op_a < op_b};  // SLT
            4'b1001: alu_result = {31'd0, $signed(op_a) < $signed(op_b)}; // SLTU
            default: alu_result = 32'd0;
        endcase
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            result <= 32'd0;
            zero_flag <= 1'b0;
        end else begin
            result <= alu_result;
            zero_flag <= (alu_result == 32'd0);
        end
    end

endmodule
"""

    def test_simple_rtl_area_estimation(self, simple_rtl):
        """Test area estimation for simple RTL."""
        from chipagent.mcp import chipagent_estimate_area

        result = chipagent_estimate_area(
            reg_code=simple_rtl,
            module_name="simple_counter"
        )

        data = json.loads(result)
        assert data["status"] == "success"
        assert "estimated_gates" in data
        assert "flip_flops" in data
        assert "complexity_score" in data
        # Simple counter should have some gates
        assert data["estimated_gates"] > 0
        assert data["complexity_score"] >= 0

    def test_simple_rtl_performance_estimation(self, simple_rtl):
        """Test performance estimation for simple RTL."""
        from chipagent.mcp import chipagent_estimate_performance

        result = chipagent_estimate_performance(
            reg_code=simple_rtl,
            target_freq_mhz=100.0,
            module_name="simple_counter"
        )

        data = json.loads(result)
        assert data["status"] == "error"
        assert "Liberty" in data["message"]
        assert "estimated_fmax_mhz" not in data

    def test_simple_rtl_power_estimation(self, simple_rtl):
        """Test power estimation for simple RTL."""
        from chipagent.mcp import chipagent_estimate_power

        result = chipagent_estimate_power(
            reg_code=simple_rtl,
            clock_freq_mhz=100.0,
            module_name="simple_counter"
        )

        data = json.loads(result)
        assert data["status"] == "success"
        assert "total_power_mw" in data
        assert "dynamic_power_mw" in data
        assert "static_power_mw" in data
        assert data["total_power_mw"] > 0
        assert data["dynamic_power_mw"] >= 0
        assert data["static_power_mw"] >= 0

    def test_simple_rtl_ppa_check_pass(self, simple_rtl):
        """Test PPA target check with achievable targets."""
        from chipagent.mcp import chipagent_check_ppa_targets

        result = chipagent_check_ppa_targets(
            reg_code=simple_rtl,
            max_gates=10000,
            min_freq_mhz=50.0,
            max_power_mw=10.0,
            clock_freq_mhz=100.0,
            module_name="simple_counter"
        )

        data = json.loads(result)
        assert data["status"] == "error"
        assert "Liberty" in data["message"]

    def test_simple_rtl_ppa_check_fail(self, simple_rtl):
        """Test PPA target check with unachievable targets."""
        from chipagent.mcp import chipagent_check_ppa_targets

        result = chipagent_check_ppa_targets(
            reg_code=simple_rtl,
            max_gates=10,  # Unreasonably low
            min_freq_mhz=10000.0,  # Unreasonably high
            max_power_mw=0.001,  # Unreasonably low
            clock_freq_mhz=100.0,
            module_name="simple_counter"
        )

        data = json.loads(result)
        assert data["status"] == "error"
        assert "Liberty" in data["message"]

    def test_complex_rtl_area_estimation(self, complex_rtl):
        """Test area estimation for complex RTL."""
        from chipagent.mcp import chipagent_estimate_area

        result = chipagent_estimate_area(
            reg_code=complex_rtl,
            module_name="riscv_alu"
        )

        data = json.loads(result)
        assert data["status"] == "success"
        assert data["estimated_gates"] > 10  # ALU should have some gates
        assert data["complexity_score"] > 0  # Should have some complexity

    def test_complex_rtl_performance_estimation(self, complex_rtl):
        """Test performance estimation for complex RTL."""
        from chipagent.mcp import chipagent_estimate_performance

        result = chipagent_estimate_performance(
            reg_code=complex_rtl,
            target_freq_mhz=200.0,
            module_name="riscv_alu"
        )

        data = json.loads(result)
        assert data["status"] == "error"
        assert "Liberty" in data["message"]
        assert "estimated_fmax_mhz" not in data

    def test_complex_rtl_power_estimation(self, complex_rtl):
        """Test power estimation for complex RTL."""
        from chipagent.mcp import chipagent_estimate_power

        result = chipagent_estimate_power(
            reg_code=complex_rtl,
            clock_freq_mhz=200.0,
            module_name="riscv_alu"
        )

        data = json.loads(result)
        assert data["status"] == "success"
        # Complex ALU should consume some power
        assert data["total_power_mw"] > 0

    def test_complex_rtl_ppa_check_with_suggestions(self, complex_rtl):
        """Test PPA target check with optimization suggestions."""
        from chipagent.mcp import chipagent_check_ppa_targets

        result = chipagent_check_ppa_targets(
            reg_code=complex_rtl,
            max_gates=500,  # Tight area constraint
            min_freq_mhz=500.0,  # High frequency target
            max_power_mw=5.0,  # Low power target
            clock_freq_mhz=200.0,
            module_name="riscv_alu"
        )

        data = json.loads(result)
        assert data["status"] == "error"
        assert "Liberty" in data["message"]

    def test_ppa_tools_work_without_eda(self, simple_rtl):
        """Test that PPA tools work without EDA tools installed."""
        from chipagent.mcp import (
            chipagent_estimate_area,
            chipagent_estimate_performance,
            chipagent_estimate_power,
            chipagent_check_ppa_targets
        )

        # Area/power text estimators remain available. Performance must fail
        # closed without Liberty instead of inventing a frequency.
        area_result = chipagent_estimate_area(
            reg_code=simple_rtl,
            module_name="simple_counter"
        )
        assert json.loads(area_result)["status"] == "success"

        perf_result = chipagent_estimate_performance(
            reg_code=simple_rtl,
            module_name="simple_counter"
        )
        assert json.loads(perf_result)["status"] == "error"

        power_result = chipagent_estimate_power(
            reg_code=simple_rtl,
            module_name="simple_counter"
        )
        assert json.loads(power_result)["status"] == "success"

        ppa_result = chipagent_check_ppa_targets(
            reg_code=simple_rtl,
            max_gates=10000,
            min_freq_mhz=50.0,
            max_power_mw=10.0,
            module_name="simple_counter"
        )
        assert json.loads(ppa_result)["status"] == "error"

    def test_riscv_cpu_ppa_analysis(self):
        """Test PPA analysis on the RISC-V CPU from earlier demo."""
        from chipagent.mcp import (
            chipagent_estimate_area,
            chipagent_estimate_performance,
            chipagent_estimate_power,
            chipagent_check_ppa_targets
        )

        # Load the RISC-V CPU RTL
        riscv_file = Path("/home/cambricon/workspace/chipagent/generated/riscv_core.v")
        if not riscv_file.exists():
            pytest.skip("RISC-V CPU RTL file not found")

        rtl_code = riscv_file.read_text()

        # Test area estimation
        area_result = chipagent_estimate_area(
            reg_code=rtl_code,
            module_name="riscv_core"
        )
        area_data = json.loads(area_result)
        assert area_data["status"] == "success"
        assert area_data["estimated_gates"] > 100  # CPU should be complex
        assert area_data["complexity_score"] >= 0  # Should have some complexity score

        # Test performance estimation
        perf_result = chipagent_estimate_performance(
            reg_code=rtl_code,
            target_freq_mhz=100.0,
            module_name="riscv_core"
        )
        perf_data = json.loads(perf_result)
        assert perf_data["status"] == "success"
        assert perf_data["estimated_fmax_mhz"] > 50  # Should achieve at least 50 MHz

        # Test power estimation
        power_result = chipagent_estimate_power(
            reg_code=rtl_code,
            clock_freq_mhz=100.0,
            module_name="riscv_core"
        )
        power_data = json.loads(power_result)
        assert power_data["status"] == "success"
        assert power_data["total_power_mw"] > 0

        # Test PPA target check
        ppa_result = chipagent_check_ppa_targets(
            reg_code=rtl_code,
            max_gates=50000,
            min_freq_mhz=100.0,
            max_power_mw=50.0,
            clock_freq_mhz=100.0,
            module_name="riscv_core"
        )
        ppa_data = json.loads(ppa_result)
        assert ppa_data["status"] == "success"
        assert "meets_all_targets" in ppa_data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
