from __future__ import annotations

import json
from pathlib import Path

import pytest

from chipagent.models import TaskObject
from chipagent.toolchain import which_tool
from chipagent.tools import ToolContext, ToolLoader
from chipagent.tools.base import ToolResult
from chipagent.tools.simulation import RunSimulationTool
from chipagent.tools.waveform_analysis import AnalyzeWaveformTool


VCD = """$date today $end
$version chipagent-test $end
$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 1 \" valid $end
$var wire 1 # ready $end
$var wire 4 $ id $end
$var wire 8 % data $end
$upscope $end
$enddefinitions $end
$dumpvars
0!
0\"
0#
b0000 $
b00000000 %
$end
#5
1!
1\"
b0011 $
b00000001 %
#10
0!
b00000010 %
#15
1!
#20
0!
1#
#25
1!
#30
0!
0\"
"""


def _task() -> TaskObject:
    return TaskObject(task_type="waveform_debug", module_name="dut", description="")


def test_waveform_tool_discovers_violations_and_transactions(tmp_path: Path) -> None:
    waveform = tmp_path / "failure.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    output = tmp_path / "report"
    result = AnalyzeWaveformTool().run(ToolContext(
        task=_task(),
        inputs={
            "waveform_path": str(waveform),
            "output_dir": str(output),
            "protocols": [{
                "name": "request",
                "clock": "tb.clk",
                "valid": "tb.valid",
                "ready": "tb.ready",
                "id": "tb.id",
                "payload": ["tb.data"],
                "max_stall_time": 8,
                "source": {"file": "RequestPipe.scala", "line": 42},
            }],
        },
    ))

    assert result.ok
    assert result.result["status"] == "issues_found"
    kinds = {item["type"] for item in result.result["findings"]}
    assert {"payload_changed_while_stalled", "stall_timeout"} <= kinds
    assert all(item["source"]["line"] == 42 for item in result.result["findings"])
    assert result.result["transactions"] == [{
        "time": 25,
        "channel": "request",
        "id": "0x3",
        "payload": {"payload:0": "0x2"},
    }]
    artifacts = result.result["artifacts"]
    failure = json.loads(Path(artifacts["failure.json"]).read_text())
    timeline = Path(artifacts["timeline.md"]).read_text()
    assert failure["timescale"] == "1ns"
    assert "payload_changed_while_stalled" in timeline
    assert "| 25 | request | handshake | 0x3 |" in timeline


def test_waveform_tool_reports_ambiguous_or_missing_signals(tmp_path: Path) -> None:
    waveform = tmp_path / "failure.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    result = AnalyzeWaveformTool().run(ToolContext(
        task=_task(),
        inputs={"waveform_path": str(waveform), "protocols": [{
            "name": "bad", "valid": "missing_valid", "ready": "tb.ready",
        }]},
    ))
    assert result.ok
    assert result.result["status"] == "issues_found"
    assert any("signal not found" in issue
               for issue in result.result["configuration_issues"])
    assert result.result["protocols_analyzed"] == 0


def test_waveform_tool_is_auto_discovered() -> None:
    assert "analyze_waveform" in {tool.name for tool in ToolLoader().discover()}


def test_simulation_tool_attaches_waveform_analysis(tmp_path: Path) -> None:
    waveform = tmp_path / "failure.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    ctx = ToolContext(task=_task(), inputs={
        "waveform_protocols": [{
            "name": "request", "clock": "tb.clk", "valid": "tb.valid",
            "ready": "tb.ready", "id": "tb.id", "payload": ["tb.data"],
        }]
    })
    result = RunSimulationTool()._attach_waveform_analysis(
        ToolResult(result={"vcd_path": str(waveform)}), ctx)
    assert result.result["waveform_analysis"]["protocols_analyzed"] == 1
    assert result.result["waveform_analysis"]["transactions"][0]["id"] == "0x3"


def test_waveform_tool_correlates_request_response_ids(tmp_path: Path) -> None:
    waveform = tmp_path / "ids.vcd"
    waveform.write_text("""$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 1 \" req_valid $end
$var wire 1 # req_ready $end
$var wire 2 $ req_id $end
$var wire 1 % rsp_valid $end
$var wire 1 & rsp_ready $end
$var wire 2 ' rsp_id $end
$upscope $end
$enddefinitions $end
$dumpvars
0!
0\"
1#
b00 $
0%
1&
b00 '
$end
#5
1!
1\"
b01 $
#10
0!
0\"
#15
1!
1%
b10 '
#20
0!
0%
""", encoding="utf-8")
    result = AnalyzeWaveformTool().run(ToolContext(
        task=_task(),
        inputs={"waveform_path": str(waveform), "protocols": [
            {"name": "req", "clock": "tb.clk", "valid": "tb.req_valid",
             "ready": "tb.req_ready", "id": "tb.req_id",
             "role": "request", "pair": "memory", "max_response_time": 6},
            {"name": "rsp", "clock": "tb.clk", "valid": "tb.rsp_valid",
             "ready": "tb.rsp_ready", "id": "tb.rsp_id",
             "role": "response", "pair": "memory"},
        ]},
    ))
    kinds = {item["type"] for item in result.result["findings"]}
    assert "response_without_request" in kinds
    assert "missing_response" in kinds


@pytest.mark.skipif(not (which_tool("iverilog") and which_tool("vvp")),
                    reason="Icarus Verilog is not installed")
def test_real_simulation_generates_and_analyzes_vcd(tmp_path: Path) -> None:
    rtl = """module dut(input logic valid, input logic ready,
                       input logic [3:0] id);
endmodule
"""
    tb = """module tb;
  logic clk = 0;
  logic valid = 0;
  logic ready = 1;
  logic [3:0] id = 0;
  dut u_dut(.valid(valid), .ready(ready), .id(id));
  always #5 clk = ~clk;
  initial begin
    $dumpfile("sim.vcd");
    $dumpvars(0, tb);
    #7 valid = 1; id = 4'h3;
    @(posedge clk); #1 valid = 0;
    #10 $display("PASS"); $finish;
  end
endmodule
"""
    result = RunSimulationTool().run(ToolContext(
        task=_task(), work_dir=str(tmp_path),
        inputs={"reg_code": rtl, "tb_code": tb, "waveform_protocols": [{
            "name": "request", "clock": "tb.clk", "valid": "tb.valid",
            "ready": "tb.ready", "id": "tb.id",
        }]},
    ))
    assert result.result["passed"] == "passed"
    analysis = result.result["waveform_analysis"]
    assert analysis["status"] == "clean", analysis["configuration_issues"]
    assert analysis["transactions"][0]["id"] == "0x3"
