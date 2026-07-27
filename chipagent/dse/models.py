"""Data model for the Design-Space-Exploration agent loop (Phase 2 DSE).

These dataclasses flow through the DSE loop:
``spec_parse → propose → build → measure → score → reflect → select``.

They are plain dataclasses (same style as :mod:`chipagent.models`) so the DSE
state stays easy to inspect and serialise.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class NonFunctionalTargets:
    """Acceptance budgets the selected design must satisfy. ``None`` = the
    objective is unconstrained for this run (still measured, not gated)."""

    area_budget_cells: Optional[float] = None
    fmax_target_mhz: Optional[float] = None
    latency_budget_cycles: Optional[float] = None
    power_budget_mw: Optional[float] = None
    # Software-side budget: max acceptable normalised CPU/polling cost [0..1].
    sw_cost_budget: Optional[float] = None

    @classmethod
    def from_constraints(cls, constraints: Dict[str, Any]) -> "NonFunctionalTargets":
        def _f(key: str) -> Optional[float]:
            v = constraints.get(key)
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return cls(
            area_budget_cells=_f("area_budget"),
            fmax_target_mhz=_f("fmax_target_mhz"),
            latency_budget_cycles=_f("latency_budget_cycles"),
            power_budget_mw=_f("power_budget_mw"),
            sw_cost_budget=_f("sw_cost_budget"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DesignSpec:
    """Parsed feature spec + non-functional targets for one DSE run."""

    module_name: str
    description: str
    functional_ops: List[str] = field(default_factory=list)
    targets: NonFunctionalTargets = field(default_factory=NonFunctionalTargets)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_name": self.module_name,
            "description": self.description,
            "functional_ops": self.functional_ops,
            "targets": self.targets.to_dict(),
        }


@dataclass
class Partition:
    """A HW/SW/idle assignment for the feature's operations."""

    hw_ops: List[str] = field(default_factory=list)
    sw_ops: List[str] = field(default_factory=list)
    idle_gated: List[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VariantConfig:
    """A point in the microarchitecture design space.

    The fields are the axes the stub measurer is sensitive to, so moving
    along any axis changes at least one measured dimension — that is what
    gives the search loop real signal.
    """

    pipelined: bool = False
    pipeline_stages: int = 1
    datapath_width: int = 8          # parallelism / bus width
    buffer_depth: int = 0            # FIFO depth on the streaming path
    clock_gating: bool = False
    interface: str = "register"      # "register" | "streaming"
    polling: bool = True             # register-interface driver style

    def signature(self) -> str:
        """Stable key for dedup; two configs with the same signature are the
        same design point and must not be resampled."""
        return "|".join([
            str(int(self.pipelined)), str(self.pipeline_stages),
            str(self.datapath_width), str(self.buffer_depth),
            str(int(self.clock_gating)), self.interface, str(int(self.polling)),
        ])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Measurement:
    """Backend + software measurement of one candidate."""

    area_cells: float = 0.0
    fmax_mhz: float = 0.0
    latency_cycles: float = 0.0
    power_mw: float = 0.0
    sw_cost: float = 0.0
    source: str = "stub"             # "stub" | "yosys" | "verilator"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Score:
    """Multi-objective score. ``dimensions`` are normalised to [0,1] against
    the spec targets (1 = meets budget). ``scalar`` is the weighted sum; lower
    Pareto rank = better (0 = non-dominated)."""

    dimensions: Dict[str, float] = field(default_factory=dict)
    scalar: float = 0.0
    pareto_rank: int = 0
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    """A fully evaluated design point: partition + variant + code + measurement."""

    variant_id: str
    partition: Partition
    variant: VariantConfig
    rtl: str
    driver: str
    measurement: Measurement
    score: Score
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "partition": self.partition.to_dict(),
            "variant": self.variant.to_dict(),
            "measurement": self.measurement.to_dict(),
            "score": self.score.to_dict(),
            "rtl_chars": len(self.rtl),
            "driver_chars": len(self.driver),
            "rationale": self.rationale,
        }
