import json

from chipagent.tools.phys_flow_asap7 import (
    _build_overview,
    _collect_qor,
    _config,
    _next_numbered_output,
)


def test_orfs_config_accepts_verilog_and_systemverilog_sources():
    config = _config("DecodePipe", 30, 0.35, "WC")

    assert "$(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v)" in config
    assert "$(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.sv)" in config
    assert "export CORNER = WC" in config
    assert "export SKIP_REPORT_METRICS = 0" in config


def test_collect_qor_includes_post_route_timing(tmp_path):
    logs = tmp_path / "logs" / "base"
    reports = tmp_path / "reports" / "base"
    logs.mkdir(parents=True)
    reports.mkdir(parents=True)
    (logs / "6_report.json").write_text(
        json.dumps(
            {
                "finish__timing__setup__tns": 0,
                "finish__timing__hold__tns": 0,
                "finish__timing__setup__ws": 251.747,
                "finish__timing__hold__ws": 74.8157,
                "finish__timing__fmax__clock:core_clock": 1.91039e9,
                "finish__timing__fmax": 1.94508e9,
            }
        )
    )

    qor = _collect_qor(tmp_path)

    assert qor["setup_worst_slack_ps"] == 251.747
    assert qor["hold_worst_slack_ps"] == 74.8157
    assert qor["core_clock_fmax_mhz"] == 1910.39
    assert qor["reported_fmax_mhz"] == 1945.08


def test_overview_leads_with_plain_pass_fail_result():
    overview = _build_overview(
        status="success",
        manifest={
            "parameters": {
                "clock_period": 1000.0,
                "corner": "WC",
            }
        },
        qor={
            "setup_worst_slack_ps": 251.747,
            "hold_worst_slack_ps": 74.8157,
            "setup_violation_count": 0,
            "hold_violation_count": 0,
            "route_drc_errors": 0,
            "core_clock_fmax_mhz": 1910.39,
        },
        diagnosis={},
    )

    assert overview["verdict"] == "PASS"
    assert overview["target"]["frequency_mhz"] == 1000.0
    assert overview["timing"]["setup_slack_ps"] == 251.747
    assert "meets 1000.0 MHz" in overview["headline"]


def test_default_output_directories_are_sequential(tmp_path):
    (tmp_path / "001_old").mkdir()
    (tmp_path / "003_other").mkdir()
    (tmp_path / "not_numbered").mkdir()

    assert _next_numbered_output(tmp_path, "DecodePipe").name == "004_DecodePipe"
