import json
import pytest


RTL = """
module passthrough(input logic clk, input logic d, output logic q);
  always_ff @(posedge clk) q <= d;
endmodule
"""


def test_performance_requires_liberty_and_never_invents_fmax():
    from chipagent.mcp import chipagent_estimate_performance

    data = json.loads(chipagent_estimate_performance(
        reg_code=RTL,
        target_freq_mhz=1000.0,
        module_name="passthrough",
    ))

    assert data["status"] == "error"
    assert "Liberty" in data["message"]
    assert "estimated_fmax_mhz" not in data
    assert "critical_path_delay_ps" not in data


def test_performance_rejects_missing_real_tools(monkeypatch):
    from chipagent.tools import synth_timing
    from chipagent.tools.base import ToolContext
    from chipagent.models import TaskObject

    monkeypatch.setattr(synth_timing, "which_tool", lambda _name: None)
    monkeypatch.setattr(
        synth_timing,
        "run_eda_command",
        lambda *args, **kwargs: type("Result", (), {"returncode": 127})(),
    )
    ctx = ToolContext(
        task=TaskObject(task_type="timing_analysis", module_name="passthrough", description=""),
        inputs={"reg_code": RTL, "liberty": "library(fake) {}"},
    )
    result = synth_timing.TimingAnalysisTool(require_sta=True).run(ctx)

    assert result.result["status"] == "error"
    assert set(result.result["required_tools"]) == {"yosys"}
    assert "estimated_fmax_mhz" not in result.result


def test_split_liberty_merge_preserves_cells_and_rejects_mixed_pvt():
    from chipagent.tools.synth_timing import _merge_liberty_texts

    base = """
library (base) {
  nom_voltage : 0.7;
  nom_temperature : 25;
  cell (NAND2) { area : 1; }
}
"""
    same_corner = """
library (inv) {
  nom_voltage : 0.7;
  nom_temperature : 25;
  cell (INV) { area : 1; }
}
"""
    merged = _merge_liberty_texts([base, same_corner])
    assert "cell (NAND2)" in merged
    assert "cell (INV)" in merged

    mixed_corner = same_corner.replace("nom_voltage : 0.7", "nom_voltage : 0.63")
    with pytest.raises(ValueError, match="PVT mismatch"):
        _merge_liberty_texts([base, mixed_corner])
