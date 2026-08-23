import json

from chipagent.tools.phys_flow_asap7 import (
    _build_overview,
    _collect_qor,
    _config,
    _high_fanout_buffer_cell,
    _manifest,
    _next_numbered_output,
    _sdc,
    _targeted_fanout_tcl,
    _rtl_source,
)


def test_rtl_source_combines_ordered_multifile_design(tmp_path):
    child = tmp_path / "child.sv"
    top = tmp_path / "top.sv"
    child.write_text("module child; endmodule\n")
    top.write_text("module top; child u(); endmodule\n")

    source = _rtl_source("", [str(child), str(top)])

    assert source.index("module child") < source.index("module top")
    assert "ChipAgent source: child.sv" in source


def test_rtl_source_rejects_non_rtl_file(tmp_path):
    source = tmp_path / "sources.f"
    source.write_text("top.sv\n")

    try:
        _rtl_source("", [str(source)])
        assert False, "expected invalid extension"
    except ValueError as exc:
        assert ".v or .sv" in str(exc)


def test_simple_report_distinguishes_global_and_core_slack():
    from chipagent.tools.phys_flow_asap7 import _summary_markdown

    overview = {
        "verdict": "FAIL",
        "headline": "timing failed",
        "next_action": "inspect paths",
        "target": {"frequency_mhz": 1000.0, "clock_period_ps": 1000.0,
                   "corner": "TC"},
        "timing": {
            "setup_slack_ps": -1400.0, "hold_slack_ps": -20.0,
            "setup_tns_ps": -1.0, "hold_tns_ps": -2.0,
            "core_clock_fmax_mhz": 500.0, "setup_violations": 2,
            "hold_violations": 3,
            "critical_path": {"startpoint": "a", "endpoint": "b",
                              "slack_ps": -900.0},
        },
        "physical": {},
    }

    report = _summary_markdown("dut", overview, {})

    assert "Worst setup slack (all groups)" in report
    assert "Core critical-path slack | -900.000 ps" in report


def test_orfs_config_accepts_verilog_and_systemverilog_sources():
    config = _config("DecodePipe", 30, 0.35, "WC")

    assert "$(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v)" in config
    assert "$(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.sv)" in config
    assert "export CORNER = WC" in config
    assert "export ASAP7_USE_VT = RVT" in config
    assert "export SKIP_REPORT_METRICS = 0" in config


def test_orfs_config_adds_macro_collateral():
    config = _config(
        "ScalarRegisterManager", 30, 0.35, "WC", has_macros=True
    )

    assert "ADDITIONAL_LEFS" in config
    assert "ADDITIONAL_LIBS" in config
    assert "GDS_ALLOW_EMPTY = fakeram.*" in config


def test_orfs_config_adds_macro_gds_and_fixed_placement():
    config = _config(
        "SharedL2Slice",
        15,
        0.30,
        "TC",
        has_macros=True,
        has_macro_gds=True,
        has_macro_placement=True,
    )

    assert "export ADDITIONAL_GDS" in config
    assert "export MACRO_PLACEMENT_TCL" in config
    assert "GDS_ALLOW_EMPTY" not in config


def test_orfs_config_can_hook_targeted_fanout_repair():
    config = _config(
        "SharedL2Slice",
        15,
        0.30,
        "TC",
        has_high_fanout_repair=True,
    )

    assert (
        "export POST_RESIZE_TCL = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/targeted_fanout.tcl"
        in config
    )


def test_orfs_closure_config_enables_timing_repairs():
    config = _config(
        "ScalarRegisterManager",
        30,
        0.35,
        "WC",
        timing_effort="closure",
        setup_slack_margin=0.02,
    )

    assert "export GPL_TIMING_DRIVEN = 1" in config
    assert "export SKIP_CTS_REPAIR_TIMING = 0" in config
    assert "export SKIP_INCREMENTAL_REPAIR = 0" in config
    assert "export SKIP_LAST_GASP = 0" in config
    assert "export REMOVE_ABC_BUFFERS = 0" in config
    assert "export SETUP_SLACK_MARGIN = 0.02" in config


def test_orfs_closure_no_cts_keeps_later_timing_repairs():
    config = _config(
        "ScalarRegisterManager",
        30,
        0.35,
        "WC",
        timing_effort="closure_no_cts",
    )

    assert "export GPL_TIMING_DRIVEN = 1" in config
    assert "export SKIP_CTS_REPAIR_TIMING = 1" in config
    assert "export SKIP_INCREMENTAL_REPAIR = 0" in config
    assert "export SKIP_LAST_GASP = 0" in config


def test_orfs_config_can_tighten_abc_delay_target():
    config = _config(
        "GpuCore",
        30,
        0.35,
        "WC",
        abc_clock_period_ps=700.0,
    )

    assert "export ABC_CLOCK_PERIOD_IN_PS = 700.0" in config


def test_orfs_config_can_select_yosys():
    config = _config(
        "GpuCore",
        30,
        0.35,
        "WC",
        synthesis_engine="yosys",
    )

    assert "export SYNTH_USE_SYN = 0" in config


def test_orfs_config_can_select_sv2v_lowered_rtl():
    config = _config("Fp32FmaLane", 30, 0.35, "WC", sv_frontend="sv2v")

    assert "$(DESIGN_NAME).sv2v.v" in config
    assert "*.sv)" not in config


def test_orfs_config_can_enable_top_level_retiming():
    config = _config(
        "Fp32FmaLane", 30, 0.35, "WC", enable_retiming=True
    )

    assert "export SYNTH_RETIME_MODULES = Fp32FmaLane" in config


def test_orfs_config_can_enable_physical_arithmetic_selection():
    config = _config(
        "Fp32FmaLane", 30, 0.35, "WC", swap_arithmetic_operators=True
    )

    assert "export OPENROAD_HIERARCHICAL = 1" in config
    assert "export SWAP_ARITH_OPERATORS = 1" in config
    assert "KOGGE_STONE" in config
    assert "BOOTH,BASE" in config


def test_orfs_config_can_select_low_vt_cells():
    config = _config("Fp32FmaLane", 30, 0.35, "WC", cell_vt="LVT")

    assert "export ASAP7_USE_VT = LVT" in config


def test_diagnose_emulated_openroad_illegal_instruction():
    from chipagent.tools.phys_flow_asap7 import _diagnose

    diagnosis = _diagnose(
        "Error: cts.tcl, 81 child killed: illegal instruction",
        status="failed",
        timeout=300,
    )

    assert "amd64" in diagnosis["root_cause"]
    assert "closure_no_cts" in diagnosis["suggested_fix"]


def test_orfs_sdc_can_constrain_max_fanout():
    sdc = _sdc("ScalarRegisterManager", "clock", 1000.0, max_fanout=10)

    assert "set_max_fanout 10 [current_design]" in sdc


def test_targeted_fanout_tcl_splits_named_nets():
    tcl = _targeted_fanout_tcl(
        ["storeTable.pendingEntry"],
        high_fanout_max=6,
    )

    assert "get_nets -hier $pattern" in tcl
    assert "insert_buffer -net $net -load_pins $group" in tcl
    assert "chipagent_split_high_fanout_net {storeTable.pendingEntry} 6" in tcl


def test_targeted_fanout_helper_is_empty_without_targets():
    assert _targeted_fanout_tcl([], 8) == ""


def test_targeted_fanout_buffer_follows_cell_vt():
    assert _high_fanout_buffer_cell("RVT") == "BUFx16f_ASAP7_75t_R"
    assert _high_fanout_buffer_cell("LVT") == "BUFx16f_ASAP7_75t_L"
    assert _high_fanout_buffer_cell("SLVT") == "BUFx16f_ASAP7_75t_SL"


def test_manifest_records_high_fanout_scope(tmp_path):
    manifest = _manifest(
        reg_code="module SharedL2Slice; endmodule",
        module_name="SharedL2Slice",
        image="chipagent/openroad:arm64",
        clock_port="clock",
        clock_period=1000.0,
        core_utilization=15,
        place_density=0.30,
        high_fanout_nets=["storeTable.pendingEntry"],
        high_fanout_max=6,
    )

    assert manifest["parameters"]["high_fanout_nets"] == [
        "storeTable.pendingEntry"
    ]
    assert manifest["parameters"]["high_fanout_max"] == 6


def test_diagnose_macro_pin_access_failure():
    from chipagent.tools.phys_flow_asap7 import _diagnose

    diagnosis = _diagnose(
        "[ERROR DRT-0073] No access point for u1/we_in (fakeram7_1rw_32x32).",
        status="failed",
        timeout=300,
    )

    assert "LEF pin" in diagnosis["root_cause"]
    assert "routing tracks" in diagnosis["suggested_fix"]


def test_diagnose_macro_pdn_failure():
    from chipagent.tools.phys_flow_asap7 import _diagnose

    diagnosis = _diagnose(
        "[ERROR PDN-0233] Failed to generate full power grid.",
        status="failed",
        timeout=300,
    )

    assert "power grid" in diagnosis["root_cause"]
    assert "SYMMETRY" in diagnosis["suggested_fix"]


def test_diagnose_gds_export_failure_without_claiming_route_drc():
    from chipagent.tools.phys_flow_asap7 import _diagnose, _is_gds_export_failure

    report = """
DetailedRoute__route__drc_errors 0
cp: cannot stat 'results/asap7/Fp32FmaLane/base/6_1_merged.gds': No such file or directory
make: *** [Makefile:703: results/asap7/Fp32FmaLane/base/6_final.gds] Error 1
"""
    assert _is_gds_export_failure(report)
    diagnosis = _diagnose(report, status="partial", timeout=1800)
    assert "GDS" in diagnosis["root_cause"]
    assert "DRC errors" not in diagnosis["root_cause"]


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
    assert qor["reported_fmax_mhz"] == 1910.39


def test_parse_critical_path_separates_cell_and_net_delay():
    from chipagent.tools.phys_flow_asap7 import _parse_critical_path

    report = """Startpoint: launch_q (rising edge-triggered flip-flop)
Endpoint: capture_q (rising edge-triggered flip-flop)
Path Group: core_clock
Path Type: max

     1    1.00   4.00   20.00   20.00 ^ launch_q/Q (DFFx1)
                  2.00    3.00   23.00 ^ add/A (AND2x1)
     1    1.00   5.00   30.00   53.00 ^ add/Y (AND2x1)
                  2.00    7.00   60.00 ^ capture_q/D (DFFx1)
                                 60.00   data arrival time
                                -10.00   slack (VIOLATED)
"""

    path = _parse_critical_path(report)

    assert path is not None
    assert path["cell_delay_ps"] == 50.0
    assert path["net_delay_ps"] == 10.0
    assert path["cell_count"] == 2
    assert path["dominant_cell_types"][0]["cell_type"] == "AND2x1"


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
