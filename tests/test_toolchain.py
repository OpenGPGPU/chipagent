import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("x86_64", "amd64"), ("amd64", "amd64"),
     ("aarch64", "arm64"), ("arm64", "arm64")],
)
def test_normalize_docker_architecture(raw, expected):
    from chipagent.toolchain import normalize_architecture

    assert normalize_architecture(raw) == expected


def test_openroad_image_selection_prefers_daemon_native_arm64(monkeypatch):
    import chipagent.toolchain as toolchain

    monkeypatch.delenv("CHIPAGENT_OPENROAD_IMAGE", raising=False)
    monkeypatch.setattr(toolchain, "docker_daemon_architecture", lambda: "arm64")
    monkeypatch.setattr(
        toolchain,
        "_inspect_image_architecture",
        lambda image: "arm64" if image.endswith(":arm64") else "amd64",
    )

    assert toolchain.configured_openroad_image() == "chipagent/openroad:arm64"


def test_openroad_image_selection_honors_explicit_override(monkeypatch):
    from chipagent.toolchain import configured_openroad_image

    monkeypatch.setenv("CHIPAGENT_OPENROAD_IMAGE", "registry/native-openroad:v1")
    assert configured_openroad_image() == "registry/native-openroad:v1"


def test_docker_image_status_rejects_wrong_architecture(monkeypatch):
    import chipagent.toolchain as toolchain

    monkeypatch.setattr(toolchain, "which_tool", lambda command: "/usr/bin/docker")
    monkeypatch.setattr(toolchain, "docker_daemon_architecture", lambda: "arm64")
    completed = SimpleNamespace(returncode=0, stdout="amd64\n", stderr="")
    with patch("chipagent.toolchain.subprocess.run", return_value=completed):
        status = toolchain.docker_image_status("chipagent/openroad:latest")

    assert status["image_available"] is True
    assert status["architecture_matches"] is False
    assert status["usable"] is False
    assert "does not match" in status["error"]


def test_toolchain_status_shape():
    from chipagent.toolchain import toolchain_status

    data = toolchain_status()

    assert data["status"] == "success"
    assert "tools" in data
    assert "missing_tools" in data
    assert "docker" in data
    assert "docker_images" in data
    assert "setup" in data
    assert "usable_count" in data
    assert any(tool["name"] == "yosys" for tool in data["tools"])
    assert all("usable" in tool for tool in data["tools"])
    assert all("available_via_docker" in tool for tool in data["tools"])
    assert data["setup"]["recommended"] == "docker"
    assert data["setup"]["docker"]["build"] == "bash scripts/setup_eda_env.sh --docker"
    assert data["setup"]["docker"]["smoke"] == "bash scripts/setup_eda_env.sh --smoke"
    assert data["setup"]["openroad_docker"]["image_env"] == "CHIPAGENT_OPENROAD_IMAGE"


def test_mcp_check_toolchain():
    from chipagent.mcp import chipagent_check_toolchain

    data = json.loads(chipagent_check_toolchain())

    assert data["status"] == "success"
    assert "tools" in data
    assert "setup" in data


def test_toolchain_scripts_exist():
    root = Path(__file__).resolve().parent.parent
    assert (root / "scripts" / "setup_eda_env.sh").exists()
    assert (root / "scripts" / "smoke_eda_env.sh").exists()
    assert (root / "Dockerfile.openroad").exists()


def test_asap7_physical_flow_tool_is_discoverable():
    from chipagent.tools import ToolLoader

    names = {tool.name for tool in ToolLoader().discover()}

    assert "run_physical_flow_asap7" in names


def test_asap7_physical_flow_missing_input():
    from chipagent.mcp import chipagent_run_physical_flow_asap7

    data = json.loads(chipagent_run_physical_flow_asap7(reg_code="", module_name="tiny"))

    assert data["status"] == "error"
    assert "reg_code" in data["message"] or "netlist" in data["message"]


def test_asap7_physical_flow_diagnoses_clock_port():
    from chipagent.tools.phys_flow_asap7 import _diagnose

    diagnosis = _diagnose(
        "[WARNING STA-0366] port 'clk' not found.\n"
        "[ERROR IFP-0065] No rows created in the core area.\n"
        "make: *** [Makefile:415: results/asap7/no_clk/base/2_1_floorplan.odb] Error 2",
        status="failed",
        timeout=300,
    )

    assert diagnosis["status"] == "diagnosed"
    assert "clock port" in diagnosis["root_cause"]


def test_asap7_physical_flow_diagnoses_timeout():
    from chipagent.tools.phys_flow_asap7 import _diagnose

    diagnosis = _diagnose("Flow timed out after 1 seconds.", status="timeout", timeout=1)

    assert diagnosis["status"] == "diagnosed"
    assert "timeout" in diagnosis["root_cause"]


def test_asap7_physical_flow_cache_helpers(tmp_path):
    from chipagent.tools.phys_flow_asap7 import _cache_valid, _manifest

    manifest = _manifest(
        reg_code="module tiny(input clk); endmodule",
        module_name="tiny",
        image="chipagent/openroad:latest",
        clock_port="clk",
        clock_period=310.0,
        core_utilization=10,
        place_density=0.20,
    )
    work = tmp_path / "orfs-work"
    (work / "results" / "base").mkdir(parents=True)
    (work / "results" / "base" / "6_final.def").write_text("DEF", encoding="utf-8")
    (work / "results" / "base" / "6_final.gds").write_text("GDS", encoding="utf-8")
    (tmp_path / "orfs_run.log").write_text("ok", encoding="utf-8")
    (tmp_path / "flow_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert _cache_valid(tmp_path, work, manifest)

    closure_manifest = _manifest(
        reg_code="module tiny(input clk); endmodule",
        module_name="tiny",
        image="chipagent/openroad:latest",
        clock_port="clk",
        clock_period=310.0,
        core_utilization=10,
        place_density=0.20,
        timing_effort="closure",
    )
    assert closure_manifest["input_hash"] != manifest["input_hash"]

    changed = dict(manifest)
    changed["input_hash"] = "different"
    assert not _cache_valid(tmp_path, work, changed)


def test_sv_parameter_dse_wrapper_uses_explicit_ports():
    from chipagent.tools.sv_dse import _wrapper

    rtl = """
    module param_adder #(parameter WIDTH = 8)(
      input [WIDTH-1:0] a,
      input [WIDTH-1:0] b,
      output [WIDTH:0] sum
    );
      assign sum = a + b;
    endmodule
    """

    code = _wrapper(rtl, "param_adder", "param_adder_dse_WIDTH4", {"WIDTH": 4})

    assert "parameter WIDTH = 4" in code
    assert ".*" not in code
    assert ".a(a)" in code
    assert ".sum(sum)" in code


def test_sv_parameter_dse_light(tmp_path):
    from chipagent.mcp import chipagent_run_sv_parameter_dse

    rtl = """
    module param_adder #(parameter WIDTH = 8)(
      input [WIDTH-1:0] a,
      input [WIDTH-1:0] b,
      output [WIDTH:0] sum
    );
      assign sum = a + b;
    endmodule
    """

    data = json.loads(chipagent_run_sv_parameter_dse(
        reg_code=rtl,
        module_name="param_adder",
        parameters={"WIDTH": [4, 8]},
        output_dir=str(tmp_path),
    ))

    assert data["status"] == "success"
    assert data["candidate_count"] == 2
    assert data["best"]["params"]["WIDTH"] == 4


def test_synthesis_persists_eda_artifacts(tmp_path):
    from chipagent.mcp import chipagent_run_synthesis

    rtl = """
    module adder(input [7:0] a, input [7:0] b, output [8:0] sum);
      assign sum = a + b;
    endmodule
    """

    data = json.loads(chipagent_run_synthesis(
        reg_code=rtl,
        module_name="adder",
        output_dir=str(tmp_path),
    ))

    if data["status"] != "success":
        return

    artifacts = data["artifacts"]
    assert Path(artifacts["netlist.v"]).exists()
    assert Path(artifacts["report.log"]).exists()
    assert Path(artifacts["synth.ys"]).exists()
    assert str(tmp_path) in artifacts["netlist.v"]


def test_run_flow_persists_report_and_artifacts(tmp_path):
    from chipagent.mcp import chipagent_run_flow

    rtl = """
    module adder(input [7:0] a, input [7:0] b, output [8:0] sum);
      assign sum = a + b;
    endmodule
    """
    tb = """
    module tb;
      reg [7:0] a;
      reg [7:0] b;
      wire [8:0] sum;
      adder dut(.a(a), .b(b), .sum(sum));
      initial begin
        a = 8'd2;
        b = 8'd3;
        #1;
        if (sum !== 9'd5) begin $display("FAIL"); $finish; end
        $display("PASS");
        $finish;
      end
    endmodule
    """

    data = json.loads(chipagent_run_flow(
        reg_code=rtl,
        tb_code=tb,
        module_name="adder",
        output_dir=str(tmp_path),
    ))

    if data["steps"]["synthesis"].get("tool_available") is False:
        assert "synthesis" in data["infrastructure_failures"]
        assert "synthesis" in data["unavailable_steps"]
        assert data["outcome"] in {"partial", "unavailable", "failed"}
        pytest.skip("requires Yosys on the host or in the configured Docker image")

    assert data["status"] == "success"
    assert data["outcome"] == "success"
    assert data["design_failures"] == []
    assert data["infrastructure_failures"] == []
    assert data["steps"]["simulation"]["passed"] == "passed"
    assert data["steps"]["synthesis"]["status"] == "success"
    assert data["steps"]["formality"]["equivalent"] is True
    assert data["steps"]["physical"]["status"] == "skipped"
    assert Path(data["artifacts"]["flow_report.json"]).exists()
    assert any(key.startswith("synthesis.") for key in data["artifacts"])


def test_flow_step_classification_separates_design_and_environment_failures():
    from chipagent.mcp import _classify_flow_steps

    result = _classify_flow_steps({
        "simulation": {
            "status": "failed",
            "passed": "failed",
            "tool": "verilator",
            "tool_available": True,
        },
        "synthesis": {
            "status": "error",
            "tool": "yosys",
            "tool_available": False,
        },
        "physical": {"status": "skipped"},
        "coverage": {"status": "passed"},
    })

    assert result["outcome"] == "failed"
    assert result["design_failures"] == ["simulation"]
    assert result["infrastructure_failures"] == ["synthesis"]
    assert result["unavailable_steps"] == ["synthesis"]
    assert result["completed_steps"] == ["coverage"]
    assert result["skipped_steps"] == ["physical"]
