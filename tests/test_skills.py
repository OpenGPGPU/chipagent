"""Tests for the text-Skill loader / runner / template engine (offline)."""
from __future__ import annotations

from chipagent.config import LLMConfig
from chipagent.llm import LLMClient
from chipagent.models import TaskObject
from chipagent.skills import RTLSkill, SkillContext, SkillLoader, TextSkill, render
from chipagent.validation import VerilogLinter


# ----------------------------------------------------------------------
# Template engine
# ----------------------------------------------------------------------
def test_render_substitutes_vars():
    out = render("module {{name}}; // {{w}} bits {{#if en}}on{{/if}}", {"name": "m", "w": 8, "en": True})
    assert out == "module m; // 8 bits on"


def test_render_if_else():
    assert render("{{#if x}}A{{#else}}B{{/if}}", {"x": True}) == "A"
    assert render("{{#if x}}A{{#else}}B{{/if}}", {"x": False}) == "B"


def test_render_missing_var_left_as_is():
    assert render("module {{unknown}};", {}) == "module {{unknown}};"


# ----------------------------------------------------------------------
# Loader discovery
# ----------------------------------------------------------------------
def test_loader_discovers_rtl_skill_with_resources():
    skills = SkillLoader().discover()
    names = [s.name for s in skills]
    assert "rtl_generation" in names
    rtl = next(s for s in skills if s.name == "rtl_generation")
    assert rtl.task_type == "rtl_generation"
    assert "reference/module_template.v" in rtl.resources
    assert "reference/lint_rules.md" in rtl.resources
    assert "examples/axi_dma_example.v" in rtl.resources
    assert rtl.template_path == "reference/module_template.v"


def test_loader_load_by_task_type_returns_none_for_unknown():
    assert SkillLoader().load("no_such_task_type") is None


# ----------------------------------------------------------------------
# TextSkill execution (offline template path)
# ----------------------------------------------------------------------
def _offline_llm():
    return LLMClient(LLMConfig(api_key=None, base_url=None, model=None, style="none", enabled=False))


def _rtl_task(**over):
    base = dict(
        task_type="rtl_generation",
        module_name="axi_dma",
        description="AXI DMA",
        interface={"type": "axi"},
        constraints={"clock": True, "reset": True, "data_width": 16, "interface": "axi"},
        output_requirements={"language": "verilog"},
    )
    base.update(over)
    return TaskObject(**base)


def test_text_skill_template_passes_lint():
    skill = TextSkill(SkillLoader().load("rtl_generation"), llm=_offline_llm())
    result = skill.run(SkillContext(task=_rtl_task(), context="", llm=_offline_llm()))
    code = result.code
    assert "module axi_dma" in code
    assert code.count("endmodule") == 1
    assert "always_ff" in code  # reset path
    lint = VerilogLinter().run(code)
    assert lint["syntax"] == "passed"
    assert lint["lint"] == "passed"


def test_text_skill_design_notes_describe_module():
    skill = TextSkill(SkillLoader().load("rtl_generation"), llm=_offline_llm())
    result = skill.run(SkillContext(task=_rtl_task(), context="", llm=_offline_llm()))
    notes = result.design_notes
    assert "axi_dma" in notes
    assert "16" in notes
    assert "rising-edge" in notes


def test_text_skill_refuses_non_rtl_task():
    skill = TextSkill(SkillLoader().load("rtl_generation"), llm=_offline_llm())
    result = skill.run(SkillContext(task=_rtl_task(task_type="unknown"), context="", llm=_offline_llm()))
    assert result.code == ""
    assert result.checks["lint"] == "skipped"


def test_text_skill_combinational_branch_when_no_clock():
    skill = TextSkill(SkillLoader().load("rtl_generation"), llm=_offline_llm())
    task = _rtl_task(constraints={"clock": False, "reset": False, "data_width": 4, "interface": "generic"})
    result = skill.run(SkillContext(task=task, context="", llm=_offline_llm()))
    assert "assign data_out" in result.code
    assert "always_ff" not in result.code


def test_rtlskill_factory_loads_text_skill():
    skill = RTLSkill(llm=_offline_llm())
    assert isinstance(skill, TextSkill)
    assert skill.name == "rtl_generation"
