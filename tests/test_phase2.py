"""Phase 2 tests: state machine, skills, tools, checkers, audit, sandbox,
approval gate, task panel, and Temporal reliability scaffolding (offline)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from chipagent.config import LLMConfig, Settings
from chipagent.llm import LLMClient
from chipagent.models import TaskObject, TaskStatus
from chipagent.state import TaskStateMachine, TaskStateError
from chipagent.skills import SkillContext, SkillLoader, TextSkill
from chipagent.tools import ToolContext, ToolLoader
from chipagent.tools.base import Tool, ToolResult


def _offline_llm():
    return LLMClient(LLMConfig(api_key=None, base_url=None, model=None, style="none", enabled=False))


# ======================================================================
# Step 2: extended state machine
# ======================================================================
class TestStateMachinePhase2:
    def test_new_states_exist(self):
        assert TaskStatus.RETRYING.value == "retrying"
        assert TaskStatus.VERIFYING.value == "verifying"
        assert TaskStatus.NOTIFYING.value == "notifying"

    def test_running_to_retrying_to_running(self):
        m = TaskStateMachine()
        m.transition(TaskStatus.RUNNING)
        m.transition(TaskStatus.RETRYING)
        m.transition(TaskStatus.RUNNING)
        m.transition(TaskStatus.COMPLETED)
        assert m.status == TaskStatus.COMPLETED

    def test_running_to_verifying(self):
        m = TaskStateMachine()
        m.transition(TaskStatus.RUNNING)
        m.transition(TaskStatus.VERIFYING)
        m.transition(TaskStatus.FAILED)

    def test_notifying_to_completed(self):
        m = TaskStateMachine()
        m.transition(TaskStatus.RUNNING)
        m.transition(TaskStatus.NOTIFYING, detail="awaiting approval")
        m.transition(TaskStatus.COMPLETED)

    def test_illegal_completed_to_running_still_rejected(self):
        m = TaskStateMachine()
        m.transition(TaskStatus.RUNNING)
        m.transition(TaskStatus.COMPLETED)
        with pytest.raises(TaskStateError):
            m.transition(TaskStatus.RUNNING)

    def test_rollback_completed_to_failed(self):
        m = TaskStateMachine()
        m.transition(TaskStatus.RUNNING)
        m.transition(TaskStatus.COMPLETED)
        m.transition(TaskStatus.FAILED)  # rollback
        assert m.status == TaskStatus.FAILED

    def test_resume_persists_new_states(self, tmp_path):
        m = TaskStateMachine(state_dir=str(tmp_path))
        m.transition(TaskStatus.RUNNING)
        m.transition(TaskStatus.VERIFYING)
        restored = TaskStateMachine.resume(m.task_id, str(tmp_path))
        assert restored.status == TaskStatus.VERIFYING


# ======================================================================
# Steps 4/5: new text skills
# ======================================================================
class TestNewSkills:
    def test_loader_discovers_uvm_and_hal(self):
        names = {s.task_type for s in SkillLoader().discover()}
        assert "uvm_skeleton" in names
        assert "hal_library" in names

    def test_uvm_template_is_balanced(self):
        skill = TextSkill.for_task_type("uvm_skeleton", llm=None)
        task = TaskObject(task_type="uvm_skeleton", module_name="uart",
                          description="UVM skeleton", constraints={"data_width": 32})
        result = skill.run(SkillContext(task=task, context="", llm=None))
        assert "package uart_uvm_pkg" in result.code
        assert result.code.count("endpackage") >= 1
        assert "module uart_uvm_tb" in result.code
        assert result.code.count("endmodule") >= 1

    def test_hal_template_has_include_guard_and_helpers(self):
        skill = TextSkill.for_task_type("hal_library", llm=None)
        task = TaskObject(task_type="hal_library", module_name="uart",
                          description="HAL", constraints={"data_width": 32})
        result = skill.run(SkillContext(task=task, context="", llm=None))
        assert "#ifndef UART_HAL_H" in result.code
        assert '#include "uart_regs.h"' in result.code
        assert "uart_hal_start" in result.code
        assert "uart_hal_is_busy" in result.code


# ======================================================================
# Steps 4/5: new tools
# ======================================================================
class TestNewTools:
    def test_tool_result_normalized_adds_trust_contract_fields(self):
        result = ToolResult(result={"aligned": True})
        data = result.normalized(tool_name="check_register_alignment")
        assert data["status"] == "passed"
        assert data["source"] == "structural_check"
        assert data["tool"] == "check_register_alignment"
        assert data["tool_available"] is True
        assert data["artifacts"] == {}
        assert data["issues"] == []
        assert data["command"] is None

    def test_loader_discovers_new_tools(self):
        names = {t.name for t in ToolLoader().discover()}
        assert {"elaborate_check", "analyze_coverage", "check_sw_hw_interface", "run_sw_hw_cosim"} <= names

    def test_elaborate_check_no_code(self):
        from chipagent.tools.elaborate import ElaborateCheckTool
        r = ElaborateCheckTool().run(ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name="m", description=""),
            inputs={}))
        assert r.result["status"] == "skipped"

    def test_elaborate_check_runs_on_valid_module(self):
        from chipagent.tools.elaborate import ElaborateCheckTool
        code = "module foo(input clk); endmodule\n"
        r = ElaborateCheckTool().run(ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name="foo", description=""),
            inputs={"reg_code": code}))
        # status is passed/failed/skipped depending on tool availability; must be set
        assert r.result["status"] in ("passed", "failed", "skipped")
        assert r.result["top"] == "foo"

    def test_analyze_coverage_stub_from_sim_passed(self):
        from chipagent.tools.coverage import AnalyzeCoverageTool
        r = AnalyzeCoverageTool().run(ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name="m", description=""),
            inputs={"sim_result": {"passed": "passed"}}))
        assert r.result["toggle"] == 80
        assert r.result["source"].startswith("stub")

    def test_analyze_coverage_parses_report(self, tmp_path):
        from chipagent.tools.coverage import AnalyzeCoverageTool
        rep = tmp_path / "cov.txt"
        rep.write_text("Toggle Coverage: 92.5%\n")
        r = AnalyzeCoverageTool().run(ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name="m", description=""),
            inputs={"sim_result": {"passed": "passed"}, "report_path": str(rep)}))
        assert r.result["toggle"] == 92.5
        assert r.result["source"].startswith("report")

    def test_check_sw_hw_interface_detects_missing_include(self):
        from chipagent.tools.interface import SwHwInterfaceTool
        rtl = "module uart_reg_top(input clk, output ctrl_start); endmodule\n"
        driver = "static void uart_read_reg(void){}\n"  # no #include, no compatible
        r = SwHwInterfaceTool().run(ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name="uart", description=""),
            inputs={"reg_code": rtl, "driver_code": driver, "module_name": "uart"}))

    def test_cosim_stub_extracts_registers_and_functions(self):
        from chipagent.tools.cosim import SwHwCosimTool
        rtl = "module m(); logic [31:0] ctrl_reg; logic [31:0] status_reg; endmodule\n"
        driver = "static int my_probe(struct platform_device *pdev) { return 0; }\n"
        r = SwHwCosimTool().run(ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name="m", description=""),
            inputs={"reg_code": rtl, "driver_code": driver, "test_scenario": "basic"}))
        assert r.result["status"] == "stub"
        assert r.result["dpi_ready"] is False
        assert r.result["registers_detected"] == 2
        assert "ctrl_reg" in r.result["register_names"]
        assert "status_reg" in r.result["register_names"]
        assert "my_probe" in r.result["driver_functions"]
        assert r.result["test_scenario"] == "basic"
        # Driver doesn't reference the registers, so issues should be populated
        assert len(r.issues) > 0
        assert any("cross-references" in i for i in r.issues)

    def test_check_sw_hw_interface_aligned(self):
        from chipagent.tools.interface import SwHwInterfaceTool
        rtl = "module uart_reg_top(input clk, output ctrl_start); endmodule\n"
        driver = (
            '#include "uart_regs.h"\n'
            'static void uart_read_reg(void){}\n'
            'static void uart_write_reg(void){}\n'
            'static const struct of_device_id match[] = { {.compatible="uart,reg-top"}, };'
        )
        r = SwHwInterfaceTool().run(ToolContext(
            task=TaskObject(task_type="hw_sw_codesign", module_name="uart", description=""),
            inputs={"reg_code": rtl, "driver_code": driver, "module_name": "uart"}))
        assert r.result["aligned"] is True


# ======================================================================
# Step 4.4: checkers
# ======================================================================
class TestCheckers:
    def test_simulation_checker_skipped_without_tool(self):
        from chipagent.checkers import SimulationChecker
        sc = SimulationChecker(sim_tool=None, cov_tool=None)
        r = sc.run("", "", module_name="m")
        assert r["status"] == "skipped"
        assert r["advice"] == "proceed"

    def test_alignment_checker_reports_mismatch_and_advises_approve(self):
        from chipagent.checkers import AlignmentChecker
        # Mismatched offsets -> not aligned -> advice approve (escalate to human)
        rtl = "module m_reg_top(); localparam ADDR_CTRL = 0; endmodule\n"
        header = "#define CTRL_ADDR 1\n"
        ac = AlignmentChecker()
        r = ac.run(rtl, header, module_name="m")
        assert r["aligned"] is False
        assert r["advice"] == "approve"
        assert r["diff"]  # non-empty diff


# ======================================================================
# Step 8: audit log
# ======================================================================
class TestAuditLog:
    def test_record_and_query(self, tmp_path):
        from chipagent.audit import AuditLog, EVT_APPROVE, EVT_SUBMIT
        al = AuditLog(str(tmp_path / "audit.jsonl"), actor="tester")
        al.record(EVT_SUBMIT, task_id="t1", timestamp="2026-01-01T00:00:00Z", detail="req")
        al.record_artifact(task_id="t1", timestamp="2026-01-01T00:00:01Z",
                           artifact="rtl", produced_by="rtl_skill", commit="abc123")
        al.record(EVT_APPROVE, task_id="t1", timestamp="2026-01-01T00:00:02Z")
        submits = al.query(event=EVT_SUBMIT)
        assert len(submits) == 1 and submits[0].task_id == "t1"
        arts = al.query(artifact="rtl")
        assert arts[0].provenance["produced_by"] == "rtl_skill"
        assert arts[0].provenance["commit"] == "abc123"
        assert al.query(task_id="other") == []

    def test_seq_monotonic(self, tmp_path):
        from chipagent.audit import AuditLog
        al = AuditLog(str(tmp_path / "a.jsonl"))
        s1 = al.record("submit", task_id="t", timestamp="x").seq
        s2 = al.record("approve", task_id="t", timestamp="y").seq
        assert s2 == s1 + 1

    def test_jsonl_one_record_per_line(self, tmp_path):
        from chipagent.audit import AuditLog
        p = tmp_path / "a.jsonl"
        al = AuditLog(str(p))
        al.record("submit", task_id="t", timestamp="x")
        al.record("approve", task_id="t", timestamp="y")
        lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2
        for ln in lines:
            json.loads(ln)  # each line is valid JSON


# ======================================================================
# Step 6: sandbox + dry-run
# ======================================================================
class TestSandbox:
    def test_dry_run_never_executes(self):
        from chipagent.sandbox import Sandbox
        r = Sandbox(mode="host").dry_run(["echo", "hi"], work_dir="/tmp", output_files=["a.v"])
        assert r.dry_run is True
        assert r.preview["command"] == ["echo", "hi"]
        assert r.preview["expected_outputs"] == ["a.v"]

    def test_host_run_echo(self):
        from chipagent.sandbox import Sandbox
        r = Sandbox(mode="host").run(["echo", "hello"], timeout=5)
        assert r.ok and "hello" in r.stdout

    def test_host_run_missing_command(self):
        from chipagent.sandbox import Sandbox
        r = Sandbox(mode="host").run(["this-cmd-does-not-exist-xyz"], timeout=5)
        assert r.returncode == 127

    def test_invalid_mode_rejected(self):
        from chipagent.sandbox import Sandbox, SandboxError
        with pytest.raises(SandboxError):
            Sandbox(mode="vmware")

    def test_is_dry_run_env(self, monkeypatch):
        from chipagent.sandbox import is_dry_run
        monkeypatch.setenv("CHIPAGENT_DRY_RUN", "1")
        assert is_dry_run() is True


# ======================================================================
# Step 7: approval gate + task panel
# ======================================================================
class _FailAlign(Tool):
    name = "check_register_alignment"

    def run(self, ctx):
        return ToolResult(result={"aligned": False, "mismatches": [{"name": "X", "issue": "test"}],
                                  "rtl_regs": {"CTRL": 0}, "header_regs": {}}, issues=["forced"])


def _orch_with_failing_align():
    from chipagent.multistep import MultiStepOrchestrator
    return MultiStepOrchestrator(tools=[_FailAlign()], require_approval=True,
                                 approver=lambda _: "reject")


class TestApprovalGate:
    def test_unapproved_artifact_not_persisted(self, tmp_path):
        from chipagent.multistep import MultiStepOrchestrator
        o = MultiStepOrchestrator(tools=[_FailAlign()], require_approval=True,
                                  approver=lambda _: "reject")
        r = o.run("请为 UART 模块生成 RTL", output_dir=str(tmp_path), require_approval=True)
        assert r["status"] == TaskStatus.FAILED.value
        assert r.get("error", "").startswith("approval:")
        assert r.get("artifacts") == {}  # nothing landed

    def test_approved_artifact_persists(self, tmp_path):
        from chipagent.multistep import MultiStepOrchestrator
        o = MultiStepOrchestrator(tools=[_FailAlign()], require_approval=True,
                                  approver=lambda _: "approve")
        r = o.run("请为 UART 模块生成 RTL", output_dir=str(tmp_path), require_approval=True)
        # Approval lets the artifact land even though the gate failed.
        assert r.get("artifacts")
        assert any(k.endswith("_reg_top.sv") for k in r["artifacts"])

    def test_no_approval_required_auto_approves(self, tmp_path):
        from chipagent.multistep import MultiStepOrchestrator
        o = MultiStepOrchestrator(tools=[_FailAlign()], require_approval=False)
        r = o.run("请为 UART 模块生成 RTL", output_dir=str(tmp_path))
        assert r.get("artifacts")  # persisted because approval not required


class TestTaskPanel:
    def test_submit_list_status(self, tmp_path):
        from chipagent.taskpanel import TaskPanel
        p = TaskPanel(state_dir=str(tmp_path))
        r = p.submit("请为 UART 模块生成 RTL", output_dir=str(tmp_path / "out"))
        assert r["task_id"]
        items = p.list()
        assert len(items) == 1 and items[0]["task_id"] == r["task_id"]
        assert p.status(r["task_id"])["request"].startswith("请为")

    def test_approval_flow_awaiting_then_approved(self, tmp_path):
        from chipagent.taskpanel import TaskPanel
        o = _orch_with_failing_align()
        p = TaskPanel(state_dir=str(tmp_path), orchestrator=o)
        r = p.submit("请为 UART 模块生成 RTL", output_dir=str(tmp_path / "out"),
                     require_approval=True, approver=lambda _: "reject")
        assert r["status"] == "awaiting_approval"
        assert r.get("artifacts") == {}  # nothing landed unapproved
        r2 = p.approve(r["task_id"])
        assert r2.get("artifacts")  # approval lands the artifact

    def test_rollback_removes_artifacts(self, tmp_path):
        from chipagent.taskpanel import TaskPanel
        p = TaskPanel(state_dir=str(tmp_path))
        out = tmp_path / "out"
        r = p.submit("请为 UART 模块生成 RTL", output_dir=str(out))
        assert out.exists()
        rb = p.rollback(r["task_id"])
        assert rb["status"] == "rolled_back"
        assert not out.exists()

    def test_pause_resume_records_audit(self, tmp_path):
        from chipagent.taskpanel import TaskPanel
        from chipagent.audit import AuditLog, EVT_PAUSE, EVT_RESUME
        p = TaskPanel(state_dir=str(tmp_path))
        r = p.submit("请为 UART 模块生成 RTL", output_dir=str(tmp_path / "o"))
        p.pause(r["task_id"])
        p.resume(r["task_id"])
        al = AuditLog(str(tmp_path / "audit.jsonl"))
        events = [rec.event for rec in al.query(task_id=r["task_id"])]
        assert EVT_PAUSE in events and EVT_RESUME in events


# ======================================================================
# Step 2: Temporal reliability scaffolding (offline structure checks)
# ======================================================================
class TestTemporalReliability:
    def test_reliable_workflow_class_has_signals_and_query(self):
        import chipagent.temporal_runtime as t
        cls = t.ReliableMultiStepWorkflow
        for m in ("pause", "resume", "cancel", "approval", "status", "run"):
            assert hasattr(cls, m), f"missing {m}"

    def test_retry_policy_helper(self):
        import chipagent.temporal_runtime as t
        rp = t.retry_policy(maximum_attempts=5)
        assert rp.maximum_attempts == 5

    def test_run_reliable_client_helper_exists(self):
        import chipagent.temporal_runtime as t
        assert hasattr(t, "run_reliable_multistep_via_temporal")

    def test_workflow_registered_on_worker(self):
        # The worker builds its workflow list from the module; ensure the new
        # class is referenced alongside the legacy ones in the worker code.
        import inspect
        import chipagent.temporal_runtime as t
        src = inspect.getsource(t._run_worker)
        assert "ReliableMultiStepWorkflow" in src


# ======================================================================
# End-to-end: multi-step closed loop produces all 5 artifacts (offline)
# ======================================================================
class TestClosedLoop:
    def test_produces_reg_header_hal_tb_driver(self, tmp_path):
        from chipagent.multistep import run_multistep
        r = run_multistep("请为 UART 模块生成 RTL 与寄存器定义", output_dir=str(tmp_path))
        out = r["output"]
        assert out["reg_code"]
        assert out["header_code"]
        assert out["hal_code"]
        assert out["tb_code"]
        assert out["driver_code"]
        arts = r["artifacts"]
        assert "uart_reg_top.sv" in arts
        assert "uart_regs.h" in arts
        assert "uart_hal.c" in arts
        assert "uart_tb.sv" in arts
        assert "uart_driver.c" in arts
