"""Tests for the MCP server (Claude Code integration)."""
from __future__ import annotations

import asyncio
import json

import pytest

# The MCP module forces CHIPAGENT_DISABLE_LLM=1 at import, so its tools run
# the deterministic path — ideal for hermetic tests.


# ======================================================================
# MCP server — tool catalogue + tool execution
# ======================================================================
class TestMCPCatalogue:
    def test_module_imports(self):
        import chipagent.mcp as m
        assert hasattr(m, "mcp")
        assert m.mcp.name == "chipagent"

    def test_all_expected_tools_registered(self):
        import chipagent.mcp as m
        tools = list(m.mcp._tool_manager._tools.keys())
        expected = [
            # 设计与生成
            "chipagent_run_dse", "chipagent_run_skill",
            # 验证与量测
            "chipagent_run_simulation", "chipagent_check_register_alignment",
            "chipagent_elaborate", "chipagent_check_sw_hw_interface",
            "chipagent_analyze_coverage", "chipagent_run_sw_hw_cosim",
            "chipagent_dry_run",
            # 能力查询
            "chipagent_list_skills", "chipagent_list_tools",
            # 任务面板
            "chipagent_task_submit", "chipagent_task_approve", "chipagent_task_rollback",
            "chipagent_task_list", "chipagent_task_status",
            "chipagent_task_pause", "chipagent_task_resume",
        ]
        for name in expected:
            assert name in tools, f"missing MCP tool {name}"

    def test_expected_resources_registered(self):
        import chipagent.mcp as m
        resources = {str(r.uri) for r in m.mcp._resource_manager._resources.values()}
        assert {
            "file://artifacts/",
            "file://register-definitions/",
            "file://audit-log/",
        } <= resources


class TestMCPTools:
    def test_list_skills_returns_json_array(self):
        from chipagent.mcp import chipagent_list_skills
        data = json.loads(chipagent_list_skills())
        names = {s["task_type"] for s in data}
        assert {"rtl_generation", "reg_definition", "hal_library"} <= names
        assert all("resources" in s for s in data)

    def test_run_skill_returns_code_and_checks(self, tmp_path):
        from chipagent.mcp import chipagent_run_skill
        r = json.loads(chipagent_run_skill("rtl_generation", {
            "module_name": "tiny_dma",
            "description": "simple DMA register shell",
            "constraints": {"data_width": 32},
            "output_dir": str(tmp_path),
        }))
        assert r["status"] == "completed"
        assert r["skill"] == "rtl_generation"
        assert "module tiny_dma" in r["code"]
        assert "elaborate" in r["checks"]
        assert r["artifacts"]["code_path"].endswith("tiny_dma.v")

    def test_list_tools_returns_names(self):
        from chipagent.mcp import chipagent_list_tools
        data = json.loads(chipagent_list_tools())
        names = {t["name"] for t in data}
        assert {"run_simulation", "check_register_alignment", "elaborate_check"} <= names

    def test_run_dse_tool_returns_tradeoff_table(self):
        from chipagent.mcp import chipagent_run_dse
        r = json.loads(chipagent_run_dse("请为 DMA 生成 RTL", area_budget=300,
                                        fmax_target_mhz=200, latency_budget_cycles=4))
        assert r["status"] == "completed"
        assert r["selected"]["variant_id"]
        assert r["tradeoff_table"]
        # The MCP tool must NOT leak full RTL blobs in the catalogue output.
        assert "rtl" not in r["selected"]

    def test_check_alignment_tool(self):
        from chipagent.mcp import chipagent_check_register_alignment
        rtl = "module m(); localparam ADDR_CTRL = 0; endmodule\n"
        hdr = "#define CTRL_ADDR 0\n"
        r = json.loads(chipagent_check_register_alignment(rtl, hdr))
        assert r["aligned"] is True

    def test_elaborate_tool_returns_status(self):
        from chipagent.mcp import chipagent_elaborate
        r = json.loads(chipagent_elaborate("module foo(input clk); endmodule\n", "foo"))
        assert r["status"] in ("passed", "failed", "skipped")
        assert r["top"] == "foo"

    def test_run_simulation_tool_missing_input(self):
        from chipagent.mcp import chipagent_run_simulation
        r = json.loads(chipagent_run_simulation("", ""))
        assert r.get("status") == "skipped"
        assert r.get("passed") == "skipped"
        assert r.get("source") == "structural_check"
        assert r.get("tool") == "run_simulation"
        assert r.get("artifacts") == {}
        assert "missing reg_code or tb_code input" in r.get("issues", [])
        assert "command" in r

    def test_task_submit_then_rollback(self, tmp_path):
        from chipagent.mcp import chipagent_task_submit, chipagent_task_rollback
        s = json.loads(chipagent_task_submit("请为 UART 生成 RTL",
                                             state_dir=str(tmp_path),
                                             output_dir=str(tmp_path / "out")))
        tid = s["task_id"]
        assert tid
        rb = json.loads(chipagent_task_rollback(tid, state_dir=str(tmp_path)))
        assert rb["status"] == "rolled_back"
        assert rb["removed"]

    def test_register_definition_resource_reads(self):
        import chipagent.mcp as m

        contents = asyncio.run(m.mcp.read_resource("file://register-definitions/"))
        payload = json.loads(contents[0].content)
        assert payload["task_type"] == "reg_definition"
        assert "reference/reg_template.sv" in payload["resources"]

    def test_analyze_coverage_stub_mode(self):
        from chipagent.mcp import chipagent_analyze_coverage
        r = json.loads(chipagent_analyze_coverage(sim_passed="passed"))
        assert r["status"] == "passed"
        assert r["toggle"] > 0
        assert r["source"].startswith("stub")
        assert r["tool"] == "analyze_coverage"
        assert r["tool_available"] is False
        assert r["artifacts"] == {}

    def test_run_sw_hw_cosim_stub(self):
        from chipagent.mcp import chipagent_run_sw_hw_cosim
        rtl = "module m(); logic [31:0] ctrl_reg; logic [31:0] status_reg; endmodule\n"
        drv = "static int my_probe(struct platform_device *pdev) { return 0; }\n"
        r = json.loads(chipagent_run_sw_hw_cosim(rtl, drv, test_scenario="basic"))
        assert r["status"] == "stub"
        assert r["dpi_ready"] is False
        assert r["registers_detected"] >= 1
        assert "ctrl_reg" in r["register_names"]

    def test_dry_run_returns_preview(self):
        from chipagent.mcp import chipagent_dry_run
        r = json.loads(chipagent_dry_run("请为 UART 生成寄存器块"))
        assert r["dry_run"] is True
        assert "parsed_task" in r
        assert "would_produce" in r
        assert "commands_that_would_run" in r
        assert len(r["commands_that_would_run"]) > 0

    def test_task_list_and_status(self, tmp_path):
        from chipagent.mcp import chipagent_task_submit, chipagent_task_list, chipagent_task_status
        s = json.loads(chipagent_task_submit("请为 UART 生成 RTL",
                                             state_dir=str(tmp_path),
                                             output_dir=str(tmp_path / "out")))
        tid = s["task_id"]
        tasks = json.loads(chipagent_task_list(state_dir=str(tmp_path)))
        assert any(t["task_id"] == tid for t in tasks)
        detail = json.loads(chipagent_task_status(tid, state_dir=str(tmp_path)))
        assert detail["task_id"] == tid

    def test_task_pause_and_resume(self, tmp_path):
        from chipagent.mcp import chipagent_task_submit, chipagent_task_pause, chipagent_task_resume
        s = json.loads(chipagent_task_submit("请为 UART 生成 RTL",
                                             state_dir=str(tmp_path),
                                             output_dir=str(tmp_path / "out")))
        tid = s["task_id"]
        p = json.loads(chipagent_task_pause(tid, state_dir=str(tmp_path)))
        assert p["task_id"] == tid
        r = json.loads(chipagent_task_resume(tid, state_dir=str(tmp_path)))
        assert r["task_id"] == tid

    def test_list_tools_discovers_cosim(self):
        from chipagent.mcp import chipagent_list_tools
        data = json.loads(chipagent_list_tools())
        names = {t["name"] for t in data}
        assert "run_sw_hw_cosim" in names


# ======================================================================
# LLM streaming (only when a gateway is reachable)
# ======================================================================
def _llm_available():
    from chipagent.config import Settings
    from chipagent.llm import LLMClient
    llm = LLMClient(Settings.load().llm)
    return llm.available


@pytest.mark.skipif(not _llm_available(), reason="no LLM gateway configured")
class TestLLMStreaming:
    def test_stream_yields_text_deltas(self):
        from chipagent.config import Settings
        from chipagent.llm import LLMClient
        llm = LLMClient(Settings.load().llm)
        chunks = list(llm.stream("Be laconic.", "Say hello.", max_tokens=32))
        assert len(chunks) >= 1
        assert "hello" in "".join(chunks).lower()

    def test_stream_offline_returns_empty(self, monkeypatch):
        from chipagent.config import LLMConfig
        from chipagent.llm import LLMClient
        llm = LLMClient(LLMConfig(api_key=None, base_url=None, model=None,
                                  style="none", enabled=False))
        chunks = list(llm.stream("sys", "hi", max_tokens=10))
        assert chunks == [""]


# ======================================================================
# CLI wiring: `chipagent task` / `chipagent dse` dispatch
# ======================================================================
class TestCLIDispatch:
    def test_workflow_main_keeps_noninteractive_dispatch(self):
        import inspect
        from chipagent import workflow
        src = inspect.getsource(workflow.main)
        assert "task" in src and "dse" in src
        assert "chat_main" not in src
