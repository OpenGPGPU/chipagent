"""PPA (Power, Performance, Area) estimation tools.

These tools provide early-stage PPA analysis without requiring real EDA tools.
They analyze RTL structure to estimate metrics and compare against targets.
"""
from .area_estimator import AreaEstimator, AreaEstimatorTool
from .performance_estimator import PerformanceEstimator, PerformanceEstimatorTool
from .power_estimator import PowerEstimator, PowerEstimatorTool
from .ppa_checker import PPATargetChecker, PPATargetCheckerTool

__all__ = [
    "AreaEstimator",
    "AreaEstimatorTool",
    "PerformanceEstimator",
    "PerformanceEstimatorTool",
    "PowerEstimator",
    "PowerEstimatorTool",
    "PPATargetChecker",
    "PPATargetCheckerTool",
]
