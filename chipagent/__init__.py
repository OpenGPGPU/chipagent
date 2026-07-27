"""ChipAgent phase 1 prototype package.

Layered architecture (see ChipAgent_Phase1_Implementation_Plan.md §6.1):

    interaction  ->  workflow (LangGraph)  ->  skills / parser
                                             repository  ->  validation
                                             state (Temporal-like)
"""
from __future__ import annotations

from .models import (
    ArtifactPaths,
    LogEntry,
    SkillOutput,
    TaskObject,
    TaskRequest,
    TaskStatus,
    WorkflowState,
)
from .workflow import run_workflow

__all__ = [
    "ArtifactPaths",
    "LogEntry",
    "SkillOutput",
    "TaskObject",
    "TaskRequest",
    "TaskStatus",
    "WorkflowState",
    "run_workflow",
]
