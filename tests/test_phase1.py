import tempfile
from pathlib import Path

from chipagent.workflow import run_workflow


def test_run_workflow_generates_rtl_for_simple_request():
    result = run_workflow("请为 AXI DMA 模块生成一个简单的 RTL 模块")

    assert result["status"] == "completed"
    assert result["task_type"] == "rtl_generation"
    assert "module axi_dma" in result["output"]["code"].lower()
    assert result["output"]["checks"]["lint"] == "passed"
    assert result["logs"][0]["step"] == "parse_request"
    assert len(result["logs"]) >= 3


def test_run_workflow_persists_artifacts_to_output_dir():
    with tempfile.TemporaryDirectory() as temp_dir:
        result = run_workflow(
            "请为 AXI DMA 模块生成一个简单的 RTL 模块",
            output_dir=temp_dir,
        )

        code_path = Path(result["artifacts"]["code_path"])
        report_path = Path(result["artifacts"]["report_path"])

        assert code_path.exists()
        assert report_path.exists()
        assert "module axi_dma" in code_path.read_text(encoding="utf-8").lower()


def test_run_workflow_reads_context_dir():
    with tempfile.TemporaryDirectory() as temp_dir:
        context_dir = Path(temp_dir)
        (context_dir / "example.md").write_text("This is repository context", encoding="utf-8")

        result = run_workflow(
            "请为 AXI DMA 模块生成一个简单的 RTL 模块",
            context_dir=str(context_dir),
        )

        assert any("repository context" in entry.get("detail", "") for entry in result["logs"] if entry.get("step") == "load_context")
