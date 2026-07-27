"""Unit tests for the individual phase 1 layers (offline, no LLM calls)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from chipagent.config import LLMConfig, Settings
from chipagent.llm import LLMClient
from chipagent.models import TaskObject, TaskStatus
from chipagent.parser import TaskParser
from chipagent.repository import Repository
from chipagent.skills import RTLSkill, SkillContext
from chipagent.state import TaskStateMachine, TaskStateError
from chipagent.validation import VerilogLinter
from chipagent.workflow import WorkflowOrchestrator


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------
def test_parser_recognises_rtl_request_and_extracts_module():
    task = TaskParser(llm=None, use_llm=False).parse("请为 AXI DMA 模块生成一个简单的 RTL 模块")
    assert task.task_type == "rtl_generation"
    assert task.module_name == "axi_dma"
    assert task.constraints.get("interface") == "axi"


def test_parser_unknown_request_is_not_rtl():
    task = TaskParser(llm=None, use_llm=False).parse("今天天气怎么样")
    assert task.task_type == "unknown"
    assert task.module_name is None


def test_parser_extracts_data_width():
    task = TaskParser(llm=None, use_llm=False).parse("请为 FIFO 模块生成一个 32-bit 的 RTL")
    assert task.task_type == "rtl_generation"
    assert task.constraints.get("data_width") == 32
    assert task.module_name == "fifo"


# ----------------------------------------------------------------------
# RTL skill (template fallback path)
# ----------------------------------------------------------------------
def _ctx(task: TaskObject):
    return SkillContext(task=task, context="", llm=LLMClient(LLMConfig(api_key=None, base_url=None, model=None, style="none", enabled=False)))


def test_rtl_skill_template_produces_balanced_module():
    task = TaskObject(
        task_type="rtl_generation",
        module_name="axi_dma",
        description="AXI DMA",
        constraints={"clock": True, "reset": True, "data_width": 8, "interface": "axi"},
    )
    result = RTLSkill().run(_ctx(task))
    assert "module axi_dma" in result.code
    assert result.code.count("endmodule") == 1
    assert "always_ff" in result.code  # reset path synthesised


def test_rtl_skill_refuses_non_rtl_task():
    task = TaskObject(task_type="unknown", module_name=None, description="?")
    result = RTLSkill().run(_ctx(task))
    assert result.code == ""
    assert result.checks["lint"] == "skipped"


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
def test_linter_passes_valid_module():
    code = "module foo(\n  input logic clk,\n  input logic rst_n\n);\nendmodule\n"
    result = VerilogLinter().run(code)
    assert result["syntax"] == "passed"
    assert result["lint"] == "passed"


def test_linter_flags_missing_endmodule():
    code = "module foo(\n  input logic clk\n);\n"
    result = VerilogLinter().run(code)
    assert result["syntax"] == "failed"
    assert any("endmodule" in i for i in result["issues"])


def test_linter_flags_unbalanced_parens():
    code = "module foo(\n  input logic clk\n;\nendmodule\n"
    result = VerilogLinter().run(code)
    assert result["syntax"] == "failed"


# ----------------------------------------------------------------------
# Repository
# ----------------------------------------------------------------------
def test_repository_reads_context_dir_and_writes_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        ctx_dir = Path(tmp) / "ctx"
        ctx_dir.mkdir()
        (ctx_dir / "spec.md").write_text("Interface spec context", encoding="utf-8")
        repo = Repository()
        context = repo.load_context(context_dir=str(ctx_dir))
        assert "Interface spec context" in context

        out_dir = Path(tmp) / "out"
        artifacts = repo.persist(
            output_dir=str(out_dir),
            task={"module_name": "axi_dma"},
            code="module axi_dma();\nendmodule\n",
            design_notes="notes",
            checks={"lint": "passed"},
            logs=[],
        )
        assert Path(artifacts["code_path"]).name == "axi_dma.v"
        assert Path(artifacts["code_path"]).exists()
        report = json.loads(Path(artifacts["report_path"]).read_text(encoding="utf-8"))
        assert report["task"]["module_name"] == "axi_dma"


# ----------------------------------------------------------------------
# State machine
# ----------------------------------------------------------------------
def test_state_machine_happy_path_and_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        machine = TaskStateMachine(state_dir=tmp)
        assert machine.status == TaskStatus.SUBMITTED
        machine.transition(TaskStatus.RUNNING, detail="start")
        machine.transition(TaskStatus.COMPLETED, detail="done")
        assert machine.status == TaskStatus.COMPLETED

        snapshot = Path(tmp) / f"{machine.task_id}.json"
        assert snapshot.exists()
        restored = TaskStateMachine.resume(machine.task_id, tmp)
        assert restored.status == TaskStatus.COMPLETED


def test_state_machine_rejects_illegal_transition():
    machine = TaskStateMachine()
    machine.transition(TaskStatus.RUNNING)
    with pytest.raises(TaskStateError):
        machine.transition(TaskStatus.SUBMITTED)  # cannot go back to submitted


def test_state_machine_allows_retry_from_failed():
    machine = TaskStateMachine()
    machine.transition(TaskStatus.RUNNING)
    machine.transition(TaskStatus.FAILED)
    machine.transition(TaskStatus.RUNNING)  # retry
    assert machine.status == TaskStatus.RUNNING


# ----------------------------------------------------------------------
# End-to-end orchestrator (offline, template path)
# ----------------------------------------------------------------------
def test_orchestrator_runs_offline_end_to_end():
    settings = Settings(llm=LLMConfig(api_key=None, base_url=None, model=None, style="none", enabled=False))
    orch = WorkflowOrchestrator(settings=settings, use_llm=False)
    with tempfile.TemporaryDirectory() as tmp:
        result = orch.run(
            "请为 AXI DMA 模块生成一个简单的 RTL 模块",
            output_dir=str(Path(tmp) / "out"),
        )
        assert result["status"] == "completed"
        assert result["task_type"] == "rtl_generation"
        assert "module axi_dma" in result["output"]["code"].lower()
        assert result["output"]["checks"]["lint"] == "passed"
        steps = [e["step"] for e in result["logs"]]
        assert steps[0] == "parse_request"
        assert "generate_rtl" in steps and "validate" in steps


def test_orchestrator_fails_gracefully_on_unknown_task():
    settings = Settings(llm=LLMConfig(api_key=None, base_url=None, model=None, style="none", enabled=False))
    orch = WorkflowOrchestrator(settings=settings, use_llm=False)
    result = orch.run("今天天气怎么样")
    assert result["status"] == "failed"
    assert result["error"] is not None
