"""Design-Space-Exploration agent loop (Phase 2 DSE).

Wraps the existing ChipAgent primitives in a search loop:
``spec_parse → propose → build → measure → score → reflect → select``.
Public entry: :func:`run_dse`.
"""
from __future__ import annotations

from .measurer import BackendMeasurer, RealBackendMeasurer, StubBackendMeasurer, default_measurer
from .models import (Candidate, DesignSpec, Measurement, NonFunctionalTargets,
                     Partition, Score, VariantConfig)
from .proposer import PartitionProposer, Reflector
from .runner import DSERunner, run_dse
from .scorer import ParetoFront, Scorer
from .variants import build_variant_driver, build_variant_rtl

__all__ = [
    "BackendMeasurer", "RealBackendMeasurer", "StubBackendMeasurer", "default_measurer",
    "Candidate", "DesignSpec", "Measurement", "NonFunctionalTargets",
    "Partition", "Score", "VariantConfig",
    "PartitionProposer", "Reflector",
    "DSERunner", "run_dse",
    "ParetoFront", "Scorer",
    "build_variant_rtl", "build_variant_driver",
]
