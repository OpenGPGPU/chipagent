"""Skill execution layer.

Skills are **text artifacts** (``text/<name>/SKILL.md`` plus reference
resources), discovered and run by the :class:`SkillLoader`/:class:`TextSkill`
machinery in :mod:`chipagent.skills.loader`. Python here is only a loader and
runner — adding a Skill means adding a directory, not writing code.
"""
from __future__ import annotations

from .base import Skill, SkillContext, SkillResult
from .loader import LoadedSkill, SkillLoader, TextSkill, render


def RTLSkill(llm=None) -> TextSkill:
    """Convenience constructor: the RTL generation text skill, ready to run.

    Kept as a function so callers can write ``RTLSkill(llm=...).run(ctx)``
    without knowing about the loader.
    """
    skill = TextSkill.for_task_type("rtl_generation", llm=llm)
    if skill is None:
        raise RuntimeError("rtl_generation skill not found under skills/text")
    return skill


__all__ = [
    "Skill",
    "SkillContext",
    "SkillResult",
    "LoadedSkill",
    "SkillLoader",
    "TextSkill",
    "RTLSkill",
    "render",
]
