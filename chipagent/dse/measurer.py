"""Backend measurement for the DSE loop (Phase 2, stub-first).

The stub is the critical piece that makes the search loop *non-theatrical*: it
maps a :class:`VariantConfig` to a :class:`Measurement` through a faithful
simplified model of the area / Fmax / latency / power tradeoffs, so moving
along any design axis changes at least one measured dimension. That gives the
reflector real signal to act on.

The :class:`RealBackendMeasurer` wraps :class:`chipagent.tools.elaborate.ElaborateCheckTool`
and overrides area / Fmax from a real yosys synthesis when yosys is on PATH —
but it is **not** the default path (user opted stub-first). It degrades
gracefully to the stub numbers when yosys is unavailable.
"""
from __future__ import annotations

from typing import Optional

from .models import Measurement, VariantConfig


class BackendMeasurer:
    """Abstract measurer: config → Measurement."""

    def measure(self, config: VariantConfig, *, module: str = "block") -> Measurement:  # pragma: no cover
        raise NotImplementedError


# Tunable constants for the simplified tradeoff model. Centralised so tests
# can assert relationships (pipelining raises Fmax, etc.) without magic numbers.
_BASE_AREA = 50.0
_AREA_PER_PIPELINE_STAGE = 40.0
_AREA_PER_WIDTH = 2.5
_AREA_PER_BUFFER = 8.0
_AREA_GATE_CELL = 6.0

_BASE_FMAX = 200.0           # MHz, combinational baseline
_FMAX_PIPELINE_GAIN = 0.12   # per stage
_FMAX_WIDTH_PENALTY = 0.04   # per width unit

_BASE_POWER = 20.0           # mW
_GATE_POWER_FACTOR = 0.6     # gating cuts 40%
_SWITCHING_PER_WIDTH = 0.3   # mW per width unit

_WORK_UNITS = 16.0           # nominal work per transaction (for latency)


class StubBackendMeasurer(BackendMeasurer):
    """Config-sensitive offline measurer."""

    def measure(self, config: VariantConfig, *, module: str = "block") -> Measurement:
        stages = max(1, config.pipeline_stages) if config.pipelined else 0
        width = max(1, config.datapath_width)

        # Area: base + pipeline regs + width-scaled datapath + buffer storage + gate.
        area = (
            _BASE_AREA
            + stages * _AREA_PER_PIPELINE_STAGE
            + width * _AREA_PER_WIDTH
            + config.buffer_depth * _AREA_PER_BUFFER
            + (_AREA_GATE_CELL if config.clock_gating else 0.0)
        )

        # Fmax: pipelining raises it (shorter critical path), wide datapath lowers it.
        fmax = _BASE_FMAX
        if config.pipelined and stages > 0:
            fmax *= 1.0 + _FMAX_PIPELINE_GAIN * stages
        fmax /= 1.0 + _FMAX_WIDTH_PENALTY * width

        # Latency: pipeline depth + work/parallelism (wider → lower latency).
        latency = (stages if config.pipelined else 1.0) + _WORK_UNITS / width

        # Power: gating cuts switching; wider datapath switches more.
        switching = _BASE_POWER + width * _SWITCHING_PER_WIDTH
        power = switching * (_GATE_POWER_FACTOR if config.clock_gating else 1.0)

        # Software cost: polling register-MMIO is expensive; interrupt cheaper;
        # streaming (interrupt-driven) is cheapest.
        if config.interface == "streaming":
            sw_cost = 0.05
        elif config.polling:
            sw_cost = 0.9
        else:  # register + interrupt
            sw_cost = 0.2

        return Measurement(
            area_cells=round(area, 1),
            fmax_mhz=round(fmax, 1),
            latency_cycles=round(latency, 2),
            power_mw=round(power, 2),
            sw_cost=sw_cost,
            source="stub",
        )


class RealBackendMeasurer(BackendMeasurer):
    """Uses yosys (via ElaborateCheckTool) for area + a critical-path Fmax
    estimate; falls back to stub for dimensions yosys can't give (power,
    sw_cost, and area/fmax when yosys is absent).

    Not the default in Phase 2 (stub-first); constructed explicitly by callers
    that want the real loop, or when ``CHIPAGENT_DSE_REAL=1``.
    """

    def __init__(self, stub: Optional[BackendMeasurer] = None) -> None:
        self._stub = stub or StubBackendMeasurer()
        self._elab = None
        try:
            from chipagent.tools.elaborate import ElaborateCheckTool
            self._elab = ElaborateCheckTool()
        except Exception:  # pragma: no cover - defensive
            self._elab = None

    def measure(self, config: VariantConfig, *, module: str = "block") -> Measurement:
        base = self._stub.measure(config, module=module)
        if self._elab is None:
            return base
        # Build the variant RTL and run yosys草估. On any failure, keep stub.
        try:
            from ..dse.variants import build_variant_rtl
            from chipagent.tools import ToolContext
            from chipagent.models import TaskObject
            code = build_variant_rtl(config, module)
            res = self._elab.run(ToolContext(
                task=TaskObject(task_type="hw_sw_codesign", module_name=module, description=""),
                inputs={"reg_code": code},
            ))
            syn = res.result.get("synthesis") or {}
            cells = syn.get("cells")
            if cells is not None:
                base.area_cells = float(cells)
                base.source = "yosys"
                # Rough Fmax proxy: more cells on the same base → lower Fmax.
                # Keep the stub's pipeline/width *direction*; scale by cell ratio.
                base.fmax_mhz = round(base.fmax_mhz * (_BASE_AREA / max(cells, 1.0)) ** 0.3, 1)
        except Exception:
            pass
        return base


def default_measurer() -> BackendMeasurer:
    """Pick the real measurer when ``CHIPAGENT_DSE_REAL=1`` and yosys is likely
    available; otherwise the stub. Keeps the offline default deterministic."""
    import os
    if os.environ.get("CHIPAGENT_DSE_REAL", "0") == "1":
        return RealBackendMeasurer()
    return StubBackendMeasurer()
