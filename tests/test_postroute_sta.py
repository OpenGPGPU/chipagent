from chipagent.tools.postroute_sta import (
    _is_boundary_path,
    _postroute_sta_tcl,
    _resolve_inputs,
)


def test_sta_tcl_reads_libs_db_spef_sdc():
    tcl = _postroute_sta_tcl(
        odb_name="5_2_route.odb",
        spef_name="6_final.spef",
        sdc_name="constraint.sdc",
        cell_vt="SLVT",
        extra_lib_names=["macro.lib"],
        max_paths=200,
    )

    assert "read_liberty /orfs/pdklib/asap7sc7p5t_AO_SLVT_TT_nldm_211120.lib.gz" in tcl
    assert "read_liberty /orfs/extralibs/macro.lib" in tcl
    assert "macros/*.lib" in tcl
    assert "read_db /orfs/results/5_2_route.odb" in tcl
    assert "read_spef /orfs/results/6_final.spef" in tcl
    assert "read_sdc /orfs/design/constraint.sdc" in tcl
    assert "group_path_count 200" in tcl


def test_sta_tcl_omits_spef_when_missing():
    tcl = _postroute_sta_tcl(
        odb_name="5_2_route.odb",
        spef_name=None,
        sdc_name="constraint.sdc",
        cell_vt="RVT",
        extra_lib_names=[],
        max_paths=50,
    )

    assert "read_spef" not in tcl
    assert "ASAP7_75t_R" not in tcl
    assert "asap7sc7p5t_SEQ_RVT_TT" in tcl


def test_boundary_path_detection():
    assert _is_boundary_path("io_req (input port clocked by vclk)", "u1/D") is True
    assert _is_boundary_path("u0/Q", "io_out (output port clocked by vclk)") is True
    assert _is_boundary_path("u0/Q (flop)", "u1/D (flop)") is False


def test_parse_sta_report_splits_io_and_internal():
    from chipagent.tools.postroute_sta import _parse_sta_report

    report = """Startpoint: io_in (input port clocked by vclk_core_clock)
Endpoint: mid_q (rising edge-triggered flip-flop)
Path Group: core_clock
Path Type: max

                                     900.00   data arrival time
                                    -100.00   slack (VIOLATED)
Startpoint: launch_q (rising edge-triggered flip-flop)
Endpoint: capture_q (rising edge-triggered flip-flop)
Path Group: core_clock
Path Type: max

                                     950.00   data arrival time
                                     -50.00   slack (VIOLATED)
Startpoint: ok_q (rising edge-triggered flip-flop)
Endpoint: done_q (rising edge-triggered flip-flop)
Path Group: core_clock
Path Type: max

                                     800.00   data arrival time
                                     200.00   slack (MET)
Startpoint: hold_q (rising edge-triggered flip-flop)
Endpoint: hold_d (rising edge-triggered flip-flop)
Path Group: core_clock
Path Type: min

                                      30.00   data arrival time
                                      -5.00   slack (VIOLATED)
"""

    parsed = _parse_sta_report(report)
    setup = parsed["setup"]
    hold = parsed["hold"]

    assert setup["wns_ps"] == -100.0
    assert setup["tns_ps"] == -150.0
    assert setup["violations"] == 2
    assert setup["boundary"]["violations"] == 1
    assert setup["boundary"]["wns_ps"] == -100.0
    assert setup["internal"]["violations"] == 1
    assert setup["internal"]["wns_ps"] == -50.0
    assert hold["violations"] == 1
    assert hold["internal"]["violations"] == 1


def test_resolve_inputs_from_physical_output_dir(tmp_path):
    base = tmp_path / "run"
    results = base / "orfs-work" / "results" / "base"
    design = base / "orfs-work" / "designs" / "asap7" / "Top"
    results.mkdir(parents=True)
    (design / "macros").mkdir(parents=True)
    (results / "5_2_route.odb").write_text("odb\n")
    (results / "6_final.spef").write_text("spef\n")
    (design / "constraint.sdc").write_text("sdc\n")

    resolved = _resolve_inputs({"physical_output_dir": str(base)})

    assert resolved["odb"].endswith("5_2_route.odb")
    assert resolved["spef"].endswith("6_final.spef")
    assert resolved["sdc"].endswith("constraint.sdc")
    assert resolved["module_name"] == "Top"
    assert resolved["macro_dir"].endswith("macros")


def test_resolve_inputs_explicit_paths_win(tmp_path):
    odb = tmp_path / "custom.odb"
    odb.write_text("odb\n")

    resolved = _resolve_inputs({
        "physical_output_dir": str(tmp_path / "missing"),
        "odb": str(odb),
    })

    assert resolved["odb"] == str(odb.resolve())
    assert "spef" not in resolved
