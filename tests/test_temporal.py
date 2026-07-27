"""Tests for the Temporal integration.

The offline tests always run (module structure + workflow/activity definitions).
The end-to-end test is only exercised when a Temporal dev server AND a worker
are reachable; otherwise it is skipped so the suite stays hermetic.
"""
from __future__ import annotations

import os
import socket

import pytest


def test_temporal_runtime_module_structure():
    import chipagent.temporal_runtime as t

    assert t.TASK_QUEUE == "chipagent-phase1"
    assert hasattr(t, "ChipAgentWorkflow")
    assert hasattr(t, "run_via_temporal")
    for act in ("act_parse", "act_load_context", "act_generate", "act_validate", "act_persist"):
        assert hasattr(t, act), f"missing activity {act}"


def _temporal_reachable() -> bool:
    host, _, port = os.environ.get("CHIPAGENT_TEMPORAL_TARGET", "localhost:7233").partition(":")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host or "localhost", int(port or 7233))) == 0


@pytest.mark.skipif(
    not _temporal_reachable(),
    reason="no Temporal server reachable at CHIPAGENT_TEMPORAL_TARGET",
)
def test_temporal_end_to_end_offline(tmp_path, monkeypatch):
    """Submit a workflow to the Temporal dev server and await its result.

    Requires a worker running on the chipagent-phase1 task queue. Start both:
        temporal server start-dev
        python -m chipagent.temporal_runtime worker
    """
    from chipagent.workflow import run_workflow

    monkeypatch.setenv("CHIPAGENT_USE_TEMPORAL", "1")
    monkeypatch.setenv("CHIPAGENT_DISABLE_LLM", "1")
    result = run_workflow(
        "请为 AXI DMA 模块生成一个简单的 RTL 模块，带时钟和复位，数据宽度 16-bit",
        output_dir=str(tmp_path),
    )
    assert result["status"] == "completed"
    assert result["task_type"] == "rtl_generation"
    assert result["output"]["checks"]["lint"] == "passed"
    assert "module axi_dma" in result["output"]["code"].lower()
    assert (tmp_path / "axi_dma.v").exists()
