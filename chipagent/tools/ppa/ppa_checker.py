"""PPA target checker and optimization advisor."""
import re
from typing import Dict, Any, List
from ..base import Tool, ToolContext, ToolResult
from .area_estimator import AreaEstimator
from .performance_estimator import PerformanceEstimator
from .power_estimator import PowerEstimator


class PPATargetChecker:
    """Checks PPA metrics against targets and provides optimization suggestions."""

    def check(self, rtl_code: str, targets: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run comprehensive PPA analysis and check against targets.

        Args:
            rtl_code: RTL source code
            targets: Dict with optional keys:
                - max_gates: Maximum gate count
                - min_freq_mhz: Minimum clock frequency
                - max_power_mw: Maximum power consumption
        """

        # Run all estimators
        area_estimator = AreaEstimator()
        performance_estimator = PerformanceEstimator()
        power_estimator = PowerEstimator()

        area_result = area_estimator.estimate(rtl_code)
        performance_result = performance_estimator.estimate(
            rtl_code,
            target_freq_mhz=targets.get("min_freq_mhz")
        )
        power_result = power_estimator.estimate(
            rtl_code,
            clock_freq_mhz=targets.get("clock_freq_mhz", 100.0)
        )

        # Check against targets
        checks = []
        meets_all_targets = True

        # Area check
        if "max_gates" in targets:
            max_gates = targets["max_gates"]
            estimated_gates = area_result["estimated_gates"]

            if estimated_gates <= max_gates:
                status = "pass"
                message = f"Area: {estimated_gates} gates (target: {max_gates}) ✓"
            elif estimated_gates <= max_gates * 1.1:  # 10% margin
                status = "warning"
                message = f"Area: {estimated_gates} gates (target: {max_gates}) - close to limit"
                meets_all_targets = False
            else:
                status = "fail"
                message = f"Area: {estimated_gates} gates (target: {max_gates}) - EXCEEDS TARGET"
                meets_all_targets = False

            checks.append({
                "metric": "area",
                "status": status,
                "message": message,
                "estimated": estimated_gates,
                "target": max_gates,
                "margin_pct": round((max_gates - estimated_gates) / max_gates * 100, 1)
            })

        # Performance check
        if "min_freq_mhz" in targets:
            min_freq = targets["min_freq_mhz"]
            estimated_freq = performance_result["estimated_fmax_mhz"]

            if estimated_freq >= min_freq:
                status = "pass"
                message = f"Performance: {estimated_freq} MHz (target: {min_freq} MHz) ✓"
            elif estimated_freq >= min_freq * 0.9:  # 10% margin
                status = "warning"
                message = f"Performance: {estimated_freq} MHz (target: {min_freq} MHz) - close to limit"
                meets_all_targets = False
            else:
                status = "fail"
                message = f"Performance: {estimated_freq} MHz (target: {min_freq} MHz) - BELOW TARGET"
                meets_all_targets = False

            checks.append({
                "metric": "performance",
                "status": status,
                "message": message,
                "estimated": estimated_freq,
                "target": min_freq,
                "margin_pct": round((estimated_freq - min_freq) / min_freq * 100, 1)
            })

        # Power check
        if "max_power_mw" in targets:
            max_power = targets["max_power_mw"]
            estimated_power = power_result["total_power_mw"]

            if estimated_power <= max_power:
                status = "pass"
                message = f"Power: {estimated_power} mW (target: {max_power} mW) ✓"
            elif estimated_power <= max_power * 1.1:  # 10% margin
                status = "warning"
                message = f"Power: {estimated_power} mW (target: {max_power} mW) - close to limit"
                meets_all_targets = False
            else:
                status = "fail"
                message = f"Power: {estimated_power} mW (target: {max_power} mW) - EXCEEDS TARGET"
                meets_all_targets = False

            checks.append({
                "metric": "power",
                "status": status,
                "message": message,
                "estimated": estimated_power,
                "target": max_power,
                "margin_pct": round((max_power - estimated_power) / max_power * 100, 1)
            })

        # Generate optimization suggestions
        suggestions = self._generate_suggestions(
            rtl_code, area_result, performance_result, power_result, checks
        )

        # Overall summary
        pass_count = sum(1 for c in checks if c["status"] == "pass")
        warning_count = sum(1 for c in checks if c["status"] == "warning")
        fail_count = sum(1 for c in checks if c["status"] == "fail")

        if meets_all_targets:
            overall_status = "pass"
            summary = f"Design meets all PPA targets ({pass_count}/{len(checks)} checks passed)"
        elif fail_count > 0:
            overall_status = "fail"
            summary = f"Design FAILS {fail_count} PPA target(s)"
        else:
            overall_status = "warning"
            summary = f"Design has {warning_count} warning(s) and meets remaining targets"

        return {
            "overall_status": overall_status,
            "meets_all_targets": meets_all_targets,
            "summary": summary,
            "checks": checks,
            "suggestions": suggestions,
            "ppa_analysis": {
                "area": area_result,
                "performance": performance_result,
                "power": power_result,
            }
        }

    def _generate_suggestions(
        self,
        rtl_code: str,
        area_result: Dict,
        performance_result: Dict,
        power_result: Dict,
        checks: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate optimization suggestions based on PPA analysis."""

        suggestions = []

        # Check each metric and suggest optimizations
        for check in checks:
            metric = check["metric"]
            status = check["status"]

            if status in ["warning", "fail"]:
                if metric == "area":
                    suggestions.extend(self._suggest_area_optimizations(rtl_code, area_result))
                elif metric == "performance":
                    suggestions.extend(self._suggest_performance_optimizations(rtl_code, performance_result))
                elif metric == "power":
                    suggestions.extend(self._suggest_power_optimizations(rtl_code, power_result))

        # Always add general best practices
        suggestions.extend(self._suggest_general_improvements(rtl_code))

        # Sort by impact
        impact_order = {"high": 0, "medium": 1, "low": 2}
        suggestions.sort(key=lambda x: impact_order.get(x.get("impact", "low"), 2))

        return suggestions

    def _suggest_area_optimizations(self, code: str, area_result: Dict) -> List[Dict]:
        """Suggest area optimizations."""
        suggestions = []

        # Check for resource sharing opportunities
        # Look for duplicate operations
        add_pattern = r'\w+\s*\+\s*\w+'
        adds = re.findall(add_pattern, code)
        if len(adds) > 5:
            suggestions.append({
                "category": "area",
                "type": "resource_sharing",
                "suggestion": "Share arithmetic units: Multiple adders detected. Consider time-multiplexing to reduce area.",
                "impact": "high",
                "trade_off": "May reduce performance due to serialization",
                "estimated_saving": "20-40% area reduction"
            })

        # Check for wide multiplexers
        case_pattern = r'\bcase\s*\([^)]+\)(.*?)endcase'
        cases = re.findall(case_pattern, code, re.IGNORECASE | re.DOTALL)
        for case_block in cases:
            items = len(re.findall(r'\w+:', case_block))
            if items > 8:
                suggestions.append({
                    "category": "area",
                    "type": "mux_optimization",
                    "suggestion": f"Large case statement ({items} items): Consider priority encoder or ROM-based implementation.",
                    "impact": "medium",
                    "trade_off": "May increase latency",
                    "estimated_saving": "10-20% area reduction for this block"
                })

        # Check register count
        if area_result["flip_flops"] > 100:
            suggestions.append({
                "category": "area",
                "type": "register_optimization",
                "suggestion": f"High register count ({area_result['flip_flops']}): Review if all registers are necessary. Consider register retiming.",
                "impact": "medium",
                "trade_off": "May affect timing",
                "estimated_saving": "5-15% area reduction"
            })

        return suggestions

    def _suggest_performance_optimizations(self, code: str, perf_result: Dict) -> List[Dict]:
        """Suggest performance optimizations."""
        suggestions = []

        # Check critical path depth
        if perf_result["critical_path_depth"] > 10:
            suggestions.append({
                "category": "performance",
                "type": "pipelining",
                "suggestion": f"Long critical path ({perf_result['critical_path_depth']} levels): Add pipeline stages to break critical path.",
                "impact": "high",
                "trade_off": "Increases latency and area",
                "estimated_improvement": "30-50% frequency improvement"
            })

        # Check for wide adders
        if perf_result["adder_bits"] > 16:
            suggestions.append({
                "category": "performance",
                "type": "adder_optimization",
                "suggestion": f"Wide adder ({perf_result['adder_bits']}-bit): Consider carry-lookahead or carry-save adder.",
                "impact": "medium",
                "trade_off": "Increases area",
                "estimated_improvement": "20-30% delay reduction"
            })

        # Check MUX depth
        if perf_result["mux_levels"] > 4:
            suggestions.append({
                "category": "performance",
                "type": "mux_optimization",
                "suggestion": f"Deep MUX tree ({perf_result['mux_levels']} levels): Consider parallel prefix or tree-based MUX.",
                "impact": "medium",
                "trade_off": "Increases area",
                "estimated_improvement": "15-25% delay reduction"
            })

        return suggestions

    def _suggest_power_optimizations(self, code: str, power_result: Dict) -> List[Dict]:
        """Suggest power optimizations."""
        suggestions = []

        # Check dynamic vs static power ratio
        if power_result["dynamic_power_pct"] > 80:
            suggestions.append({
                "category": "power",
                "type": "clock_gating",
                "suggestion": "High dynamic power: Add clock gating to disable clocks for inactive modules.",
                "impact": "high",
                "trade_off": "Increases design complexity",
                "estimated_saving": "30-50% dynamic power reduction"
            })

            suggestions.append({
                "category": "power",
                "type": "operand_isolation",
                "suggestion": "Add operand isolation gates to prevent switching when data is not valid.",
                "impact": "medium",
                "trade_off": "Small area overhead",
                "estimated_saving": "10-20% dynamic power reduction"
            })

        # Check if clock power is dominant
        breakdown = power_result["power_breakdown"]
        if breakdown["clock_tree_mw"] > power_result["total_power_mw"] * 0.3:
            suggestions.append({
                "category": "power",
                "type": "clock_tree_optimization",
                "suggestion": "Clock tree consumes >30% of power: Consider multi-clock domains or reduced clock rate for non-critical paths.",
                "impact": "high",
                "trade_off": "Increases design complexity",
                "estimated_saving": "20-40% total power reduction"
            })

        # Check for always-on logic
        if power_result["static_power_pct"] > 20:
            suggestions.append({
                "category": "power",
                "type": "power_gating",
                "suggestion": "High leakage power: Consider power gating for inactive modules.",
                "impact": "high",
                "trade_off": "Requires wake-up latency and area overhead",
                "estimated_saving": "50-80% static power reduction for gated modules"
            })

        return suggestions

    def _suggest_general_improvements(self, code: str) -> List[Dict]:
        """Suggest general design improvements."""
        suggestions = []

        # Check for complex expressions
        complex_pattern = r'[+\-&|^~]{3,}'  # 3+ operators in sequence
        complex_exprs = len(re.findall(complex_pattern, code))
        if complex_exprs > 10:
            suggestions.append({
                "category": "general",
                "type": "code_quality",
                "suggestion": "Complex expressions detected: Consider breaking into smaller sub-expressions for better readability and optimization.",
                "impact": "low",
                "trade_off": "None",
                "estimated_benefit": "Improved maintainability"
            })

        # Check for magic numbers
        magic_pattern = r'(?<![\w.])(\d+)(?![\w.])'
        numbers = re.findall(magic_pattern, code)
        unique_numbers = set(int(n) for n in numbers if int(n) > 10)
        if len(unique_numbers) > 20:
            suggestions.append({
                "category": "general",
                "type": "code_quality",
                "suggestion": f"Many magic numbers ({len(unique_numbers)}): Define as parameters or localparams for better maintainability.",
                "impact": "low",
                "trade_off": "None",
                "estimated_benefit": "Improved maintainability and reusability"
            })

        return suggestions


class PPATargetCheckerTool(Tool):
    """MCP tool wrapper for PPATargetChecker."""

    name = "check_ppa_targets"
    description = "Comprehensive PPA analysis with target checking and optimization suggestions"

    def run(self, ctx: ToolContext) -> ToolResult:
        """Run comprehensive PPA analysis and check against targets."""
        rtl_code = ctx.inputs.get("rtl_code", "")
        targets = ctx.inputs.get("targets", {})

        if not rtl_code:
            return ToolResult(
                result={"status": "error", "message": "No RTL code provided"},
                issues=["Missing input: rtl_code"]
            )

        checker = PPATargetChecker()
        result = checker.check(rtl_code, targets)

        return ToolResult(
            result={
                "status": "success",
                "message": result["summary"],
                **result
            }
        )
