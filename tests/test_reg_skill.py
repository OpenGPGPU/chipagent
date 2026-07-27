"""Tests for the reg_definition text skill and its parser detection (offline)."""
from __future__ import annotations

from chipagent.config import LLMConfig
from chipagent.llm import LLMClient
from chipagent.models import TaskObject
from chipagent.parser import TaskParser
from chipagent.skills import SkillContext, SkillLoader, TextSkill
from chipagent.validation import VerilogLinter
from chipagent.workflow import WorkflowOrchestrator


def _offline_llm():
    return LLMClient(LLMConfig(api_key=None, base_url=None, model=None, style="none", enabled=False))


def _reg_task(**over):
    base = dict(
        task_type="reg_definition",
        module_name="uart",
        description="UART 寄存器定义，包含 CTRL 和 STATUS",
        interface={"type": "generic"},
        constraints={"clock": True, "reset": True, "data_width": 32},
        output_requirements={"language": "systemverilog"},
    )
    base.update(over)
    return TaskObject(**base)


# ----------------------------------------------------------------------
# Parser detects reg_definition
# ----------------------------------------------------------------------
def test_parser_classifies_register_definition_request():
    task = TaskParser(llm=None, use_llm=False).parse("请为 UART 寄存器块生成定义，数据宽度 32-bit")
    assert task.task_type == "reg_definition"
    assert task.module_name == "uart"
    assert task.constraints.get("data_width") == 32


def test_parser_keeps_rtl_for_register_module_when_not_a_definition():
    # "寄存器模块生成 RTL" is an RTL module task, not a register-block definition.
    task = TaskParser(llm=None, use_llm=False).parse("请为寄存器堆模块生成 RTL")
    assert task.task_type == "rtl_generation"


# ----------------------------------------------------------------------
# Loader discovers both skills
# ----------------------------------------------------------------------
def test_loader_discovers_reg_skill():
    names = [s.name for s in SkillLoader().discover()]
    assert "reg_definition" in names
    assert "rtl_generation" in names
    reg = SkillLoader().load("reg_definition")
    assert reg.template_path == "reference/reg_template.sv"
    assert "reference/lint_rules.md" in reg.resources


# ----------------------------------------------------------------------
# reg_definition skill execution (offline template)
# ----------------------------------------------------------------------
def test_reg_skill_template_passes_lint():
    skill = TextSkill(SkillLoader().load("reg_definition"), llm=_offline_llm())
    result = skill.run(SkillContext(task=_reg_task(), context="", llm=_offline_llm()))
    code = result.code
    assert "module uart_reg_top" in code
    assert code.count("endmodule") == 1
    assert "always_ff" in code and "always_comb" in code
    lint = VerilogLinter().run(code)
    assert lint["syntax"] == "passed" and lint["lint"] == "passed"


def test_reg_skill_design_notes_describe_registers():
    skill = TextSkill(SkillLoader().load("reg_definition"), llm=_offline_llm())
    result = skill.run(SkillContext(task=_reg_task(), context="", llm=_offline_llm()))
    notes = result.design_notes
    assert "uart_reg_top" in notes
    assert "CTRL" in notes and "STATUS" in notes


def test_reg_skill_refuses_non_reg_task():
    skill = TextSkill(SkillLoader().load("reg_definition"), llm=_offline_llm())
    result = skill.run(SkillContext(task=_reg_task(task_type="rtl_generation"), context="", llm=_offline_llm()))
    assert result.code == ""
    assert result.checks["lint"] == "skipped"


# ----------------------------------------------------------------------
# End-to-end reg workflow (offline)
# ----------------------------------------------------------------------
def test_orchestrator_runs_reg_definition_offline():
    settings = WorkflowOrchestrator().settings  # reuse default settings
    from chipagent.config import Settings
    orch = WorkflowOrchestrator(
        settings=Settings(llm=LLMConfig(api_key=None, base_url=None, model=None, style="none", enabled=False)),
        use_llm=False,
    )
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        result = orch.run("请为 UART 寄存器块生成定义，数据宽度 32-bit", output_dir=str(Path(tmp) / "out"))
    assert result["status"] == "completed"
    assert result["task_type"] == "reg_definition"
    assert result["output"]["checks"]["lint"] == "passed"
    assert "module" in result["output"]["code"]
