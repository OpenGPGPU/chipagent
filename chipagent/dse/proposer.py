"""Partition proposer + reflector for the DSE loop (Phase 2).

Both are **rule-driven first** (deterministic, testable) with an LLM hook that
can expand / critique proposals when a gateway is available — the same
offline-fallback pattern as :class:`chipagent.skills.TextSkill` and
:mod:`chipagent.parser` (check ``available``, ``try/except LLMError``, fall
back to rules).

- :class:`PartitionProposer` produces the initial seed set of
  :class:`VariantConfig` spanning the design axes, plus a :class:`Partition`
  derived from the spec.
- :class:`Reflector` inspects the current best candidate's most-violated
  dimension and proposes a *targeted* revised :class:`VariantConfig` to try in
  the next iteration — this is what makes the loop an *agent* (propose →
  measure → critique → revise) rather than a grid search.
"""
from __future__ import annotations

from typing import List, Optional

from ..llm import LLMClient, LLMError
from ..models import TaskObject
from .models import DesignSpec, NonFunctionalTargets, Partition, VariantConfig


# Seeded design points spanning the axes. Each is a distinct region of the
# tradeoff space so the first iteration already has signal.
_SEED_CONFIGS = [
    VariantConfig(pipelined=False, pipeline_stages=0, datapath_width=8,
                  buffer_depth=0, clock_gating=False, interface="register",
                  polling=True),   # minimal combinational, low area, low Fmax
    VariantConfig(pipelined=True, pipeline_stages=2, datapath_width=8,
                  buffer_depth=0, clock_gating=False, interface="register",
                  polling=True),   # pipelined-narrow, higher Fmax, more area
    VariantConfig(pipelined=True, pipeline_stages=3, datapath_width=32,
                  buffer_depth=0, clock_gating=False, interface="streaming",
                  polling=False),  # parallel-wide streaming, low latency, big area
    VariantConfig(pipelined=True, pipeline_stages=2, datapath_width=16,
                  buffer_depth=4, clock_gating=True, interface="streaming",
                  polling=False),  # gated low-power streaming
    VariantConfig(pipelined=False, pipeline_stages=0, datapath_width=16,
                  buffer_depth=0, clock_gating=True, interface="register",
                  polling=False), # gated combinational, low power, interrupt driver
]


class PartitionProposer:
    """Produces initial (partition, variant) proposals for a spec."""

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self._llm = llm

    def initial_partition(self, spec: DesignSpec) -> Partition:
        """Rule-based HW/SW/idle split.

        The heavy datapath ops (the ones the spec names, or a sensible default
        set) go to HW; control/config stays in SW; idle-capable ops are gated.
        """
        ops = spec.functional_ops or ["datapath", "control", "status", "dma_move"]
        hw = [o for o in ops if any(k in o for k in ("data", "move", "compute", "dma"))]
        if not hw:
            hw = ops[:1]
        sw = [o for o in ops if o not in hw] or ["control", "config"]
        gated = [o for o in hw if "idle" in o or "status" in o]
        rationale = (
            f"HW: {hw} (hot datapath); SW: {sw} (control/config); "
            f"gate: {gated} (idle-capable)."
        )
        return Partition(hw_ops=hw, sw_ops=sw, idle_gated=gated, rationale=rationale)

    def seed_configs(self, spec: DesignSpec) -> List[VariantConfig]:
        """The seeded design points. If the LLM is available it can extend
        this set; otherwise the deterministic seed set is returned."""
        seeds = list(_SEED_CONFIGS)
        if self._llm is not None and self._llm.available:
            extra = self._llm_propose(spec, seeds)
            seeds.extend(extra)
        return seeds

    def _llm_propose(self, spec: DesignSpec, existing: List[VariantConfig]) -> List[VariantConfig]:
        """Ask the LLM for additional variant configs (JSON), fall back to none."""
        import json
        sys_prompt = (
            "You propose microarchitecture variants for HW/SW co-design. "
            "Reply with ONLY a JSON array of objects, each having keys: "
            "pipelined(bool), pipeline_stages(int), datapath_width(int), "
            "buffer_depth(int), clock_gating(bool), interface('register'|'streaming'), "
            "polling(bool)."
        )
        user = (f"module={spec.module_name} ops={spec.functional_ops} "
                f"targets={spec.targets.to_dict()} existing={[c.to_dict() for c in existing]}")
        try:
            raw = self._llm.chat(sys_prompt, user, temperature=0.3, max_tokens=1024)
            arr = self._extract_json_array(raw)
            out = []
            for d in arr:
                if isinstance(d, dict):
                    out.append(VariantConfig(
                        pipelined=bool(d.get("pipelined", False)),
                        pipeline_stages=int(d.get("pipeline_stages", 1)),
                        datapath_width=int(d.get("datapath_width", 8)),
                        buffer_depth=int(d.get("buffer_depth", 0)),
                        clock_gating=bool(d.get("clock_gating", False)),
                        interface=str(d.get("interface", "register")),
                        polling=bool(d.get("polling", True)),
                    ))
            return out
        except LLMError:
            return []

    @staticmethod
    def _extract_json_array(text: str) -> list:
        import re
        m = re.search(r"\[[\s\S]*\]", text)
        if not m:
            return []
        try:
            return json.loads(m.group(0))
        except Exception:
            return []


class Reflector:
    """Critiques the current best candidate and proposes a revised config.

    The revision targets the worst (most-violated) dimension — see
    :meth:`Scorer.worst_dimension`. If no dimension is violated, the reflector
    returns ``None`` (the search has converged on a feasible point; further
    exploration is the proposer's job, not the reflector's).
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self._llm = llm

    def revise(self, current: VariantConfig, worst_dim: Optional[str],
               measurement=None) -> Optional[VariantConfig]:
        if worst_dim is None:
            return None
        rule = self._rule_revision(current, worst_dim)
        if rule is not None:
            return rule
        return None  # LLM hook would go here; rule coverage is exhaustive below.

    @staticmethod
    def _rule_revision(c: VariantConfig, worst: str) -> Optional[VariantConfig]:
        v = VariantConfig(**c.to_dict())  # copy
        if worst == "area":
            # reduce width first, then drop a pipeline stage, then shrink buffer
            if v.datapath_width > 4:
                v.datapath_width = max(4, v.datapath_width // 2)
            elif v.pipelined and v.pipeline_stages > 1:
                v.pipeline_stages -= 1
            elif v.buffer_depth > 0:
                v.buffer_depth = max(0, v.buffer_depth // 2)
            else:
                return None
        elif worst == "latency":
            # Lower latency = more parallelism. In the stub model pipelining
            # *adds* latency (stages + work/width), so only widen the datapath
            # (and switch to streaming if not already, which decouples latency
            # via the buffer). Never add pipeline stages for latency.
            if v.datapath_width < 128:
                v.datapath_width *= 2
            elif v.interface != "streaming":
                v.interface = "streaming"
                v.polling = False
            else:
                return None
        elif worst == "fmax":
            # add pipelining to shorten critical path
            if not v.pipelined:
                v.pipelined = True
                v.pipeline_stages = 2
            elif v.pipeline_stages < 6:
                v.pipeline_stages += 1
            else:
                return None
        elif worst == "power":
            # enable gating, then lower width
            if not v.clock_gating:
                v.clock_gating = True
            elif v.datapath_width > 4:
                v.datapath_width = max(4, v.datapath_width // 2)
            else:
                return None
        elif worst == "sw_cost":
            # switch to streaming (interrupt), else non-polling register
            if v.interface != "streaming":
                v.interface = "streaming"
                v.polling = False
            elif v.polling:
                v.polling = False
            else:
                return None
        else:
            return None
        return v if v.signature() != c.signature() else None
