"""Performance estimation tool based on critical path analysis."""
import re
from typing import Dict, Any
from ..base import Tool, ToolContext, ToolResult


class PerformanceEstimator:
    """Estimates performance from RTL code structure analysis."""

    # Typical delays per logic level (in picoseconds)
    DELAY_PER_LOGIC_LEVEL_PS = 150  # ~150ps per level in 28nm process
    DELAY_PER_MUX_LEVEL_PS = 200    # MUX has slightly higher delay
    DELAY_PER_ADDER_BIT_PS = 100    # Ripple carry adder delay per bit

    def estimate(self, rtl_code: str, target_freq_mhz: float = None) -> Dict[str, Any]:
        """Analyze RTL code and estimate performance metrics."""

        # Analyze critical path
        critical_path = self._analyze_critical_path(rtl_code)

        # Estimate maximum frequency
        # fmax = 1 / (critical_path_delay + setup_time + clock_skew)
        setup_time_ps = 100   # Typical DFF setup time
        clock_skew_ps = 50    # Typical clock skew

        total_delay_ps = (
            critical_path["depth"] * self.DELAY_PER_LOGIC_LEVEL_PS +
            critical_path["mux_levels"] * self.DELAY_PER_MUX_LEVEL_PS +
            critical_path["adder_bits"] * self.DELAY_PER_ADDER_BIT_PS +
            setup_time_ps +
            clock_skew_ps
        )

        estimated_fmax_mhz = 1000000 / total_delay_ps  # Convert ps to MHz

        # Assess timing risk
        if target_freq_mhz:
            if estimated_fmax_mhz >= target_freq_mhz * 1.2:
                timing_risk = "low"
            elif estimated_fmax_mhz >= target_freq_mhz:
                timing_risk = "medium"
            else:
                timing_risk = "high"
        else:
            # No target specified, just categorize
            if estimated_fmax_mhz >= 500:
                timing_risk = "low"
            elif estimated_fmax_mhz >= 200:
                timing_risk = "medium"
            else:
                timing_risk = "high"

        return {
            "estimated_fmax_mhz": round(estimated_fmax_mhz, 2),
            "critical_path_delay_ps": round(total_delay_ps, 2),
            "critical_path_depth": critical_path["depth"],
            "mux_levels": critical_path["mux_levels"],
            "adder_bits": critical_path["adder_bits"],
            "timing_risk": timing_risk,
            "target_freq_mhz": target_freq_mhz,
            "slack_mhz": round(estimated_fmax_mhz - target_freq_mhz, 2) if target_freq_mhz else None,
            "analysis_details": {
                "logic_depth": f"{critical_path['depth']} logic levels",
                "mux_depth": f"{critical_path['mux_levels']} MUX levels",
                "adder_width": f"{critical_path['adder_bits']}-bit adder in critical path",
                "timing_assessment": f"Timing risk: {timing_risk}"
            }
        }

    def _analyze_critical_path(self, code: str) -> Dict[str, Any]:
        """Analyze critical path in RTL code."""

        # Count logic depth in always blocks
        logic_depth = self._estimate_logic_depth(code)

        # Count MUX levels (case/if statements)
        mux_levels = self._count_mux_levels(code)

        # Detect wide adders in critical path
        adder_bits = self._detect_wide_adders(code)

        return {
            "depth": logic_depth,
            "mux_levels": mux_levels,
            "adder_bits": adder_bits
        }

    def _estimate_logic_depth(self, code: str) -> int:
        """Estimate maximum logic depth from always blocks."""

        # Find all always blocks
        always_pattern = r'\balways(?:_ff)?\s+@\s*\([^)]+\)\s*begin(.*?)end'
        blocks = re.findall(always_pattern, code, re.IGNORECASE | re.DOTALL)

        max_depth = 0

        for block in blocks:
            # Count sequential assignments (depth indicator)
            # Look for chains of assignments
            lines = block.split(';')

            # Track dependency depth
            depth = 0
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Count operators in the line (logic depth)
                operators = len(re.findall(r'[+\-&|^~]|<<|>>|==|!=|<|>|&&|\|\|', line))

                # Each operator adds to logic depth
                # But parallel operations don't add depth
                # Heuristic: assume 1/3 of operators are sequential
                depth += max(1, operators // 3)

            max_depth = max(max_depth, depth)

        # Minimum depth of 1
        return max(1, max_depth)

    def _count_mux_levels(self, code: str) -> int:
        """Count MUX levels from case/if statements."""

        # Count case statements (each case ≈ 1 MUX level)
        case_pattern = r'\bcase\s*\('
        cases = len(re.findall(case_pattern, code, re.IGNORECASE))

        # Count nested if statements
        # Pattern: if (...) ... else if (...) ... else
        if_pattern = r'\bif\s*\('
        ifs = len(re.findall(if_pattern, code, re.IGNORECASE))

        # Estimate MUX depth
        # Case statements are typically 1 level
        # If-else chains can be deeper
        mux_depth = cases + (ifs // 2)  # Average if-else chain length

        return max(1, mux_depth)

    def _detect_wide_adders(self, code: str) -> int:
        """Detect wide adders that might be in critical path."""

        # Look for adder operations on wide signals
        # Pattern: signal[N:0] <= signal + signal
        add_pattern = r'(\w+)\s*(?:<=|=)\s*\w+\s*\+\s*\w+'
        adds = re.findall(add_pattern, code, re.IGNORECASE)

        max_width = 0

        for signal in adds:
            # Find signal declaration to get width
            # Pattern: reg/logic [N:0] signal
            decl_pattern = rf'\b(?:reg|logic|wire)\s+\[(\d+):(\d+)\]\s+{signal}\b'
            decl_match = re.search(decl_pattern, code, re.IGNORECASE)

            if decl_match:
                width = abs(int(decl_match.group(1)) - int(decl_match.group(2))) + 1
                max_width = max(max_width, width)

        # If no wide adders found, assume 8-bit as default
        return max(8, max_width)


class PerformanceEstimatorTool(Tool):
    """MCP tool wrapper for PerformanceEstimator."""

    name = "estimate_performance"
    description = "Estimate performance metrics from RTL code structure (no EDA tools required)"

    def run(self, ctx: ToolContext) -> ToolResult:
        """Run performance estimation on RTL code."""
        rtl_code = ctx.inputs.get("rtl_code", "")
        target_freq = ctx.inputs.get("target_freq_mhz")

        if not rtl_code:
            return ToolResult(
                result={"status": "error", "message": "No RTL code provided"},
                issues=["Missing input: rtl_code"]
            )

        estimator = PerformanceEstimator()
        result = estimator.estimate(rtl_code, target_freq)

        # Determine message based on timing risk
        if result["timing_risk"] == "low":
            message = f"Performance estimation: {result['estimated_fmax_mhz']} MHz (low risk)"
        elif result["timing_risk"] == "medium":
            message = f"Performance estimation: {result['estimated_fmax_mhz']} MHz (medium risk)"
        else:
            message = f"Performance estimation: {result['estimated_fmax_mhz']} MHz (HIGH RISK - may not meet timing)"

        return ToolResult(
            result={
                "status": "success",
                "message": message,
                **result
            }
        )
