"""Multi-objective scorer + Pareto front for the DSE loop (Phase 2).

Each measured dimension is normalised against the spec's targets: ``1.0`` means
"meets budget exactly"; below 1.0 = violation; above 1.0 = headroom. The
scalar is a weighted sum (default equal weights → neutral). Pareto rank is by
non-domination (rank 0 = non-dominated = the Pareto front).

Lower-is-better dimensions (area/latency/power/sw_cost) invert: meeting budget
= 1.0, exceeding = <1.0. Higher-is-better (fmax) does not invert.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .models import Candidate, Measurement, NonFunctionalTargets, Score, VariantConfig


# Lower-is-better objectives flip the ratio so "1.0 = meets budget".
_LOWER_IS_BETTER = {"area", "latency", "power", "sw_cost"}


def _norm_lower(value: float, budget: Optional[float]) -> float:
    """Lower-is-better: meeting budget = 1.0; exceeding = <1.0; headroom is
    capped at 1.0 (no bonus for far exceeding a lax budget — otherwise lax
    dimensions would inflate the scalar and drown out tight ones)."""
    if budget is None or budget <= 0:
        return 1.0  # unconstrained → neutral, no violation
    return min(1.0, budget / value) if value > 0 else 1.0


def _norm_higher(value: float, target: Optional[float]) -> float:
    """Higher-is-better: meeting target = 1.0; below = <1.0; capped at 1.0."""
    if target is None or target <= 0:
        return 1.0
    return min(1.0, value / target)


class Scorer:
    """Maps (Measurement, NonFunctionalTargets) → Score."""

    def __init__(self, weights: Optional[Dict[str, float]] = None) -> None:
        # Default equal weights across the four objectives.
        self.weights = weights or {
            "area": 0.25, "fmax": 0.25, "latency": 0.25,
            "power": 0.15, "sw_cost": 0.10,
        }

    def score(self, m: Measurement, targets: NonFunctionalTargets) -> Score:
        dims: Dict[str, float] = {
            "area": _norm_lower(m.area_cells, targets.area_budget_cells),
            "fmax": _norm_higher(m.fmax_mhz, targets.fmax_target_mhz),
            "latency": _norm_lower(m.latency_cycles, targets.latency_budget_cycles),
            "power": _norm_lower(m.power_mw, targets.power_budget_mw),
            "sw_cost": _norm_lower(m.sw_cost, targets.sw_cost_budget),
        }
        scalar = sum(self.weights.get(k, 0.0) * v for k, v in dims.items())
        violations: List[str] = [k for k, v in dims.items() if v < 1.0 - 1e-9]
        return Score(dimensions=dims, scalar=round(scalar, 4), pareto_rank=0,
                     violations=violations)

    def worst_dimension(self, s: Score) -> Optional[str]:
        """The most-violated dimension (lowest normalised value below 1.0).
        Returns ``None`` if no dimension is violated."""
        violated = {k: v for k, v in s.dimensions.items() if v < 1.0 - 1e-9}
        if not violated:
            return None
        return min(violated, key=violated.get)


class ParetoFront:
    """Maintains the non-dominated candidate set across iterations.

    A candidate **a** dominates **b** iff a is >= b on every normalised
    dimension and strictly > on at least one. The front is the set no other
    candidate dominates.
    """

    def __init__(self) -> None:
        self._members: List[Candidate] = []

    @property
    def members(self) -> List[Candidate]:
        return list(self._members)

    def update(self, candidates: List[Candidate]) -> None:
        """Fold new candidates into the front; prune any now-dominated."""
        pool = list(self._members) + [c for c in candidates if c is not None]
        front: List[Candidate] = []
        seen_sigs = set()
        for c in pool:
            sig = c.variant.signature()
            if sig in seen_sigs:
                continue
            if any(self._dominates(other, c) for other in pool if other is not c):
                continue  # c is dominated by something
            front.append(c)
            seen_sigs.add(sig)
        # De-duplicate the front itself (a non-dominated set can have dup sigs
        # only if update was called with redundant entries; guard anyway).
        self._members = front

    def dominates_any(self, candidate: Candidate) -> bool:
        return any(self._dominates(m, candidate) for m in self._members)

    @staticmethod
    def _dominates(a: Candidate, b: Candidate) -> bool:
        da, db = a.score.dimensions, b.score.dimensions
        keys = set(da) | set(db)
        ge = all(da.get(k, 0.0) >= db.get(k, 0.0) - 1e-9 for k in keys)
        gt = any(da.get(k, 0.0) > db.get(k, 0.0) + 1e-9 for k in keys)
        return ge and gt

    def assign_ranks(self, all_candidates: List[Candidate]) -> None:
        """Set ``pareto_rank`` on every candidate (0 = front). Non-dominated
        candidates get rank 0; the rest get rank = number that dominate them."""
        for c in all_candidates:
            c.score.pareto_rank = sum(
                1 for other in all_candidates
                if other is not c and self._dominates(other, c)
            )
