"""Area estimation tool based on RTL structure analysis."""
import re
from typing import Dict, Any
from ..base import Tool, ToolContext, ToolResult


class AreaEstimator:
    """Estimates area from RTL code structure analysis."""

    # Equivalent gate counts for different elements
    GATES_PER_FLIP_FLOP = 6  # DFF ≈ 6 gates
    GATES_PER_ADDER_BIT = 4  # Full adder ≈ 4 gates
    GATES_PER_MUX_2TO1 = 2   # 2-to-1 MUX ≈ 2 gates
    GATES_PER_SRAM_BIT = 8   # SRAM cell ≈ 8 gates (with peripheral)

    def estimate(self, rtl_code: str) -> Dict[str, Any]:
        """Analyze RTL code and estimate area metrics."""

        # Count flip-flops (registers)
        flip_flops = self._count_flip_flops(rtl_code)

        # Count memory bits
        memory_bits = self._count_memory_bits(rtl_code)

        # Estimate combinational logic complexity
        combo_gates = self._estimate_combinational_logic(rtl_code)

        # Calculate total estimated gates
        register_gates = flip_flops * self.GATES_PER_FLIP_FLOP
        memory_gates = memory_bits * self.GATES_PER_SRAM_BIT // 8  # SRAM is more efficient per bit

        total_gates = register_gates + combo_gates + memory_gates

        # Calculate complexity score (0-100)
        # Based on typical design sizes:
        # < 1000 gates: very simple (score 10)
        # 1000-10000 gates: simple (score 30)
        # 10000-100000 gates: medium (score 60)
        # 100000-1000000 gates: complex (score 80)
        # > 1000000 gates: very complex (score 95)
        if total_gates < 1000:
            complexity_score = 10
        elif total_gates < 10000:
            complexity_score = 30
        elif total_gates < 100000:
            complexity_score = 60
        elif total_gates < 1000000:
            complexity_score = 80
        else:
            complexity_score = 95

        return {
            "estimated_gates": total_gates,
            "register_gates": register_gates,
            "combinational_gates": combo_gates,
            "memory_gates": memory_gates,
            "flip_flops": flip_flops,
            "register_bits": flip_flops,
            "memory_bits": memory_bits,
            "complexity_score": complexity_score,
            "analysis_details": {
                "register_analysis": f"{flip_flops} flip-flops detected",
                "memory_analysis": f"{memory_bits} bits of memory detected",
                "logic_analysis": f"Estimated {combo_gates} combinational gates",
            }
        }

    def _count_flip_flops(self, code: str) -> int:
        """Count flip-flops in RTL code."""
        count = 0

        # Pattern 1: always @(posedge clk) or always_ff @(posedge clk)
        # Match: always/always_ff @(posedge clk) ... reg/logic <= ...
        ff_patterns = [
            r'always\s+@\s*\(\s*posedge\s+\w+\s*\)',  # always @(posedge clk)
            r'always_ff\s+@\s*\(\s*posedge\s+\w+\s*\)',  # always_ff @(posedge clk)
        ]

        for pattern in ff_patterns:
            matches = re.findall(pattern, code, re.IGNORECASE)
            count += len(matches)

        # Pattern 2: Count register/logic declarations (SystemVerilog compatible)
        # Match: reg/logic [N:0] name;
        reg_pattern = r'\b(?:reg|logic)\s+(?:\[\d+:\d+\]\s+)?\w+\s*;'
        reg_matches = re.findall(reg_pattern, code, re.IGNORECASE)

        # Estimate bits from register declarations
        for match in reg_matches:
            bit_match = re.search(r'\[(\d+):(\d+)\]', match)
            if bit_match:
                high = int(bit_match.group(1))
                low = int(bit_match.group(2))
                count += abs(high - low) + 1
            else:
                count += 1  # Single bit register

        # Avoid double counting - take the larger estimate
        return max(len(re.findall(ff_patterns[0], code, re.IGNORECASE)),
                   len(reg_matches))

    def _count_memory_bits(self, code: str) -> int:
        """Count memory bits from array declarations."""
        total_bits = 0

        # Pattern: reg [WIDTH-1:0] name [DEPTH-1:0];
        # Example: reg [7:0] mem [0:255]; -> 8 * 256 = 2048 bits
        mem_pattern = r'\breg\s+\[(\d+):(\d+)\]\s+\w+\s+\[(\d+):(\d+)\]'

        matches = re.findall(mem_pattern, code, re.IGNORECASE)
        for match in matches:
            width = abs(int(match[0]) - int(match[1])) + 1
            depth = abs(int(match[2]) - int(match[3])) + 1
            total_bits += width * depth

        return total_bits

    def _estimate_combinational_logic(self, code: str) -> int:
        """Estimate combinational logic gates."""
        gate_count = 0

        # Count assign statements (continuous assignments)
        assign_pattern = r'\bassign\s+\w+'
        assigns = re.findall(assign_pattern, code, re.IGNORECASE)

        # Each assign typically has 2-10 gates depending on complexity
        # Estimate based on expression complexity
        for assign in assigns:
            # Simple heuristic: count operators in the line
            line_start = code.find(assign)
            line_end = code.find(';', line_start)
            if line_end == -1:
                line_end = line_start + 50
            line = code[line_start:line_end]

            # Count operators
            operators = len(re.findall(r'[+\-&|^~]|<<|>>|==|!=|<|>|&&|\|\|', line))

            # Estimate gates: 2-5 gates per operator
            gate_count += max(2, operators * 3)

        # Count case/if statements (combinational logic in always blocks)
        case_pattern = r'\bcase\s*\('
        if_pattern = r'\bif\s*\('

        cases = len(re.findall(case_pattern, code, re.IGNORECASE))
        ifs = len(re.findall(if_pattern, code, re.IGNORECASE))

        # Each case/if typically represents a MUX
        # Case with N items ≈ log2(N) * MUX gates
        gate_count += cases * 10  # Average case statement
        gate_count += ifs * 4     # Average if statement

        return gate_count


class AreaEstimatorTool(Tool):
    """MCP tool wrapper for AreaEstimator."""

    name = "estimate_area"
    description = "Estimate area metrics from RTL code structure (no EDA tools required)"

    def run(self, ctx: ToolContext) -> ToolResult:
        """Run area estimation on RTL code."""
        rtl_code = ctx.inputs.get("rtl_code", "")

        if not rtl_code:
            return ToolResult(
                result={"status": "error", "message": "No RTL code provided"},
                issues=["Missing input: rtl_code"]
            )

        estimator = AreaEstimator()
        result = estimator.estimate(rtl_code)

        return ToolResult(
            result={
                "status": "success",
                "message": f"Area estimation completed: {result['estimated_gates']} estimated gates",
                **result
            }
        )
