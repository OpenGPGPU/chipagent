"""Skill base classes.

A Skill is the unit of execution in the execution layer (Section 3.3). It
receives a parsed :class:`TaskObject` plus repository context and returns a
:class:`SkillResult` containing generated code, design notes, and any
self-checks the skill performed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..llm import LLMClient
from ..models import TaskObject


@dataclass
class SkillContext:
    """Inputs handed to a Skill at execution time."""

    task: TaskObject
    context: str = ""
    llm: Optional[LLMClient] = None


@dataclass
class SkillResult:
    """Standardised Skill output consumed by the validation + return nodes."""

    code: str
    design_notes: str = ""
    checks: Dict[str, Any] = field(default_factory=dict)


class Skill:
    """Base Skill interface. Subclasses implement :meth:`run`."""

    name: str = "skill"

    def run(self, ctx: SkillContext) -> SkillResult:  # pragma: no cover - abstract
        raise NotImplementedError
