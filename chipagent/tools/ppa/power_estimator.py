"""Power estimation tool based on activity analysis."""
import re
from typing import Dict, Any
from ..base import Tool, ToolContext, ToolResult


class PowerEstimator:
    """Estimates power from RTL code structure analysis."""

    # Power parameters for 28nm process (typical values)
    DYNAMIC_POWER_PER_GATE_UW = 0.5      # 0.5 μW per gate at 100 MHz
    STATIC_POWER_PER_GATE_UW = 0.05      # 0.05 μW per gate (leakage)
    CLOCK_POWER_PER_FF_UW = 0.3          # 0.3 μW per FF for clock tree

    # Activity factors (probability of switching per clock cycle)
    ACTIVITY_CONTROL = 0.1    # Control signals: 10% activity
    ACTIVITY_DATA = 0.3       # Data paths: 30% activity
    ACTIVITY_CLOCK = 1.0      # Clock: 100% activity
    ACTIVITY_MEMORY = 0.2     # Memory: 20% activity

    def estimate(self, rtl_code: str, clock_freq_mhz: float = 100.0,
                 activity_profile: Dict[str, float] = None) -> Dict[str, Any]:
        """Analyze RTL code and estimate power metrics."""

        # Analyze design structure
        structure = self._analyze_structure(rtl_code)

        # Calculate dynamic power
        # P_dynamic = α * C * V^2 * f
        # Simplified: P_dynamic = activity * gates * power_per_gate * (freq/100)
        freq_factor = clock_freq_mhz / 100.0

        control_power = (
            structure["control_gates"] *
            self.DYNAMIC_POWER_PER_GATE_UW *
            self.ACTIVITY_CONTROL *
            freq_factor
        )

        data_power = (
            structure["data_gates"] *
            self.DYNAMIC_POWER_PER_GATE_UW *
            self.ACTIVITY_DATA *
            freq_factor
        )

        memory_power = (
            structure["memory_gates"] *
            self.DYNAMIC_POWER_PER_GATE_UW *
            self.ACTIVITY_MEMORY *
            freq_factor
        )

        clock_power = (
            structure["flip_flops"] *
            self.CLOCK_POWER_PER_FF_UW *
            self.ACTIVITY_CLOCK *
            freq_factor
        )

        dynamic_power_mw = (control_power + data_power + memory_power + clock_power) / 1000.0

        # Calculate static power (leakage)
        total_gates = structure["total_gates"]
        static_power_mw = (total_gates * self.STATIC_POWER_PER_GATE_UW) / 1000.0

        # Total power
        total_power_mw = dynamic_power_mw + static_power_mw

        # Power breakdown percentages
        dynamic_pct = (dynamic_power_mw / total_power_mw * 100) if total_power_mw > 0 else 0
        static_pct = (static_power_mw / total_power_mw * 100) if total_power_mw > 0 else 0

        return {
            "total_power_mw": round(total_power_mw, 3),
            "dynamic_power_mw": round(dynamic_power_mw, 3),
            "static_power_mw": round(static_power_mw, 3),
            "dynamic_power_pct": round(dynamic_pct, 1),
            "static_power_pct": round(static_pct, 1),
            "clock_freq_mhz": clock_freq_mhz,
            "power_breakdown": {
                "control_logic_mw": round(control_power / 1000.0, 3),
                "data_path_mw": round(data_power / 1000.0, 3),
                "memory_mw": round(memory_power / 1000.0, 3),
                "clock_tree_mw": round(clock_power / 1000.0, 3),
            },
            "structure_analysis": {
                "total_gates": total_gates,
                "control_gates": structure["control_gates"],
                "data_gates": structure["data_gates"],
                "memory_gates": structure["memory_gates"],
                "flip_flops": structure["flip_flops"],
            },
            "analysis_details": {
                "dynamic_breakdown": f"Control: {control_power/1000:.3f}mW, Data: {data_power/1000:.3f}mW, Memory: {memory_power/1000:.3f}mW, Clock: {clock_power/1000:.3f}mW",
                "leakage_ratio": f"{static_pct:.1f}% of total power",
                "frequency_impact": f"Power scales linearly with frequency (current: {clock_freq_mhz} MHz)"
            }
        }

    def _analyze_structure(self, code: str) -> Dict[str, int]:
        """Analyze RTL structure to categorize gates."""

        # Count flip-flops
        flip_flops = self._count_flip_flops(code)

        # Count memory bits
        memory_bits = self._count_memory_bits(code)

        # Estimate total gates (simplified)
        # Use same logic as AreaEstimator
        control_gates = self._estimate_control_logic(code)
        data_gates = self._estimate_data_path(code)
        memory_gates = memory_bits * 8 // 8  # SRAM gates

        total_gates = flip_flops * 6 + control_gates + data_gates + memory_gates

        return {
            "total_gates": total_gates,
            "control_gates": control_gates,
            "data_gates": data_gates,
            "memory_gates": memory_gates,
            "flip_flops": flip_flops,
        }

    def _count_flip_flops(self, code: str) -> int:
        """Count flip-flops in RTL code."""
        # Simplified version - same logic as AreaEstimator
        ff_pattern = r'\balways(?:_ff)?\s+@\s*\(\s*posedge\s+\w+\s*\)'
        reg_pattern = r'\breg\s+(?:\[\d+:\d+\]\s+)?\w+\s*;'

        ff_matches = len(re.findall(ff_pattern, code, re.IGNORECASE))
        reg_matches = len(re.findall(reg_pattern, code, re.IGNORECASE))

        return max(ff_matches, reg_matches)

    def _count_memory_bits(self, code: str) -> int:
        """Count memory bits from array declarations."""
        mem_pattern = r'\breg\s+\[(\d+):(\d+)\]\s+\w+\s+\[(\d+):(\d+)\]'
        matches = re.findall(mem_pattern, code, re.IGNORECASE)

        total_bits = 0
        for match in matches:
            width = abs(int(match[0]) - int(match[1])) + 1
            depth = abs(int(match[2]) - int(match[3])) + 1
            total_bits += width * depth

        return total_bits

    def _estimate_control_logic(self, code: str) -> int:
        """Estimate control logic gates."""
        # Control logic: state machines, decoders, control signals
        gate_count = 0

        # Count case statements (decoders)
        case_pattern = r'\bcase\s*\('
        cases = len(re.findall(case_pattern, code, re.IGNORECASE))
        gate_count += cases * 20  # Each case ≈ 20 gates

        # Count if statements (control logic)
        if_pattern = r'\bif\s*\('
        ifs = len(re.findall(if_pattern, code, re.IGNORECASE))
        gate_count += ifs * 10  # Each if ≈ 10 gates

        # Count state machine patterns
        # Pattern: case (state) ... STATE_IDLE: ... STATE_RUN: ...
        state_pattern = r'\bcase\s*\(\s*state\s*\)'
        states = len(re.findall(state_pattern, code, re.IGNORECASE))
        gate_count += states * 50  # State machines are complex

        return gate_count

    def _estimate_data_path(self, code: str) -> int:
        """Estimate data path gates."""
        gate_count = 0

        # Count arithmetic operations
        # Adders, subtractors, multipliers
        add_pattern = r'[+\-]'
        adds = len(re.findall(add_pattern, code))
        gate_count += adds * 4  # Each add ≈ 4 gates per bit (assume 8-bit average)

        # Count shifters
        shift_pattern = r'<<|>>'
        shifts = len(re.findall(shift_pattern, code))
        gate_count += shifts * 8  # Shifters are simpler

        # Count logical operations
        logic_pattern = r'[&|^~]'
        logics = len(re.findall(logic_pattern, code))
        gate_count += logics * 2  # Basic gates

        # Count comparisons
        comp_pattern = r'==|!=|<|>|<=|>='
        comps = len(re.findall(comp_pattern, code))
        gate_count += comps * 6  # Comparators

        # Count assignments (data routing)
        assign_pattern = r'\bassign\s+\w+'
        assigns = len(re.findall(assign_pattern, code, re.IGNORECASE))
        gate_count += assigns * 5  # Each assign ≈ 5 gates

        return gate_count


class PowerEstimatorTool(Tool):
    """MCP tool wrapper for PowerEstimator."""

    name = "estimate_power"
    description = "Estimate power consumption from RTL code structure (no EDA tools required)"

    def run(self, ctx: ToolContext) -> ToolResult:
        """Run power estimation on RTL code."""
        rtl_code = ctx.inputs.get("rtl_code", "")
        clock_freq = ctx.inputs.get("clock_freq_mhz", 100.0)
        activity_profile = ctx.inputs.get("activity_profile")

        if not rtl_code:
            return ToolResult(
                result={"status": "error", "message": "No RTL code provided"},
                issues=["Missing input: rtl_code"]
            )

        estimator = PowerEstimator()
        result = estimator.estimate(rtl_code, clock_freq, activity_profile)

        message = (
            f"Power estimation: {result['total_power_mw']} mW "
            f"({result['dynamic_power_pct']:.1f}% dynamic, {result['static_power_pct']:.1f}% static)"
        )

        return ToolResult(
            result={
                "status": "success",
                "message": message,
                **result
            }
        )
