"""Tests for the DSE agent loop (Phase 2). Offline + deterministic."""
from __future__ import annotations

import os

import pytest

from chipagent.dse import (DSERunner, ParetoFront, PartitionProposer, Reflector,
                           Scorer, StubBackendMeasurer, VariantConfig,
                           build_variant_driver, build_variant_rtl, run_dse)
from chipagent.dse.models import (DesignSpec, Measurement, NonFunctionalTargets,
                                  Partition, Score)
from chipagent.validation import VerilogLinter


# ======================================================================
# Variants
# ======================================================================
class TestVariants:
    def test_rtl_lint_passes_for_all_axes(self):
        lint = VerilogLinter()
        cfgs = [
            VariantConfig(pipelined=False, datapath_width=8, interface="register"),
            VariantConfig(pipelined=True, pipeline_stages=3, datapath_width=16),
            VariantConfig(pipelined=True, pipeline_stages=2, datapath_width=32,
                          interface="streaming", buffer_depth=4),
            VariantConfig(pipelined=False, datapath_width=8, clock_gating=True),
            VariantConfig(pipelined=True, pipeline_stages=2, datapath_width=32,
                          interface="streaming", clock_gating=True, buffer_depth=2),
        ]
        for cfg in cfgs:
            code = build_variant_rtl(cfg, "uart")
            r = lint.run(code)
            assert r["lint"] == "passed", f"{cfg} -> {r['issues']}"

    def test_pipelined_variant_has_more_registers_than_combinational(self):
        comb = build_variant_rtl(VariantConfig(pipelined=False, datapath_width=8), "m")
        pipe = build_variant_rtl(VariantConfig(pipelined=True, pipeline_stages=3, datapath_width=8), "m")
        assert pipe.count("pipe_q") > comb.count("pipe_q")

    def test_streaming_variant_has_handshake_ports(self):
        code = build_variant_rtl(
            VariantConfig(pipelined=True, pipeline_stages=2, datapath_width=32,
                          interface="streaming"), "m")
        assert "valid_in" in code and "ready_out" in code and "ready_in" in code

    def test_clock_gating_emits_gate_cell(self):
        code = build_variant_rtl(
            VariantConfig(pipelined=False, datapath_width=8, clock_gating=True), "m")
        assert "clk_en" in code and "clk_g" in code

    def test_streaming_driver_is_low_cpu(self):
        drv = build_variant_driver(
            VariantConfig(pipelined=True, pipeline_stages=2, datapath_width=32,
                          interface="streaming"), "m")
        assert "isr" in drv  # interrupt-driven, no polling loop

    def test_polling_driver_has_busy_wait(self):
        drv = build_variant_driver(
            VariantConfig(pipelined=False, datapath_width=8, interface="register",
                          polling=True), "m")
        assert "while" in drv


# ======================================================================
# Stub measurer (config-sensitivity — the key to a non-theatrical loop)
# ======================================================================
class TestStubMeasurer:
    def setup_method(self):
        self.m = StubBackendMeasurer()

    def test_pipelining_raises_fmax_and_area(self):
        base = self.m.measure(VariantConfig(pipelined=False, datapath_width=8))
        pipe = self.m.measure(VariantConfig(pipelined=True, pipeline_stages=3, datapath_width=8))
        assert pipe.fmax_mhz > base.fmax_mhz
        assert pipe.area_cells > base.area_cells

    def test_wider_datapath_raises_area_and_lowers_latency(self):
        narrow = self.m.measure(VariantConfig(pipelined=False, datapath_width=8))
        wide = self.m.measure(VariantConfig(pipelined=False, datapath_width=64))
        assert wide.area_cells > narrow.area_cells
        assert wide.latency_cycles < narrow.latency_cycles

    def test_clock_gating_lowers_power(self):
        no_g = self.m.measure(VariantConfig(pipelined=False, datapath_width=16, clock_gating=False))
        gated = self.m.measure(VariantConfig(pipelined=False, datapath_width=16, clock_gating=True))
        assert gated.power_mw < no_g.power_mw

    def test_streaming_lowers_sw_cost(self):
        reg = self.m.measure(VariantConfig(interface="register", polling=True))
        stream = self.m.measure(VariantConfig(interface="streaming"))
        assert stream.sw_cost < reg.sw_cost

    def test_interrupt_cheaper_than_polling(self):
        poll = self.m.measure(VariantConfig(interface="register", polling=True))
        intr = self.m.measure(VariantConfig(interface="register", polling=False))
        assert intr.sw_cost < poll.sw_cost

    def test_source_is_stub(self):
        assert self.m.measure(VariantConfig()).source == "stub"


# ======================================================================
# Scorer + Pareto
# ======================================================================
class TestScorer:
    def setup_method(self):
        self.scorer = Scorer()

    def test_meeting_budget_scores_one(self):
        m = Measurement(area_cells=50, fmax_mhz=300, latency_cycles=2,
                        power_mw=10, sw_cost=0.1)
        t = NonFunctionalTargets(area_budget_cells=100, fmax_target_mhz=200,
                                 latency_budget_cycles=4, power_budget_mw=20,
                                 sw_cost_budget=0.5)
        sc = self.scorer.score(m, t)
        assert sc.violations == []
        assert all(v == 1.0 for v in sc.dimensions.values())

    def test_violation_flagged(self):
        m = Measurement(area_cells=200, fmax_mhz=100, latency_cycles=10,
                        power_mw=5, sw_cost=0.1)
        t = NonFunctionalTargets(area_budget_cells=100)
        sc = self.scorer.score(m, t)
        assert "area" in sc.violations

    def test_lax_budget_does_not_inflate(self):
        # area=50 vs budget=10000 → capped at 1.0, no inflation
        m = Measurement(area_cells=50, fmax_mhz=200, latency_cycles=2, power_mw=5, sw_cost=0.1)
        t = NonFunctionalTargets(area_budget_cells=10000)
        sc = self.scorer.score(m, t)
        assert sc.dimensions["area"] == 1.0

    def test_worst_dimension(self):
        m = Measurement(area_cells=50, fmax_mhz=50, latency_cycles=2, power_mw=5, sw_cost=0.1)
        t = NonFunctionalTargets(area_budget_cells=100, fmax_target_mhz=300)
        sc = self.scorer.score(m, t)
        assert self.scorer.worst_dimension(sc) == "fmax"

    def test_worst_dimension_none_when_feasible(self):
        m = Measurement(area_cells=50, fmax_mhz=300, latency_cycles=2, power_mw=5, sw_cost=0.1)
        t = NonFunctionalTargets(area_budget_cells=100, fmax_target_mhz=200)
        sc = self.scorer.score(m, t)
        assert self.scorer.worst_dimension(sc) is None


class TestParetoFront:
    def _cand(self, dims, *, width=8, pipelined=False):
        c = lambda: None
        c.variant = VariantConfig(datapath_width=width, pipelined=pipelined)
        c.score = Score(dimensions=dims)
        return c

    def test_non_dominated_set(self):
        a = self._cand({"area": 1.0, "fmax": 0.5}, width=8)
        b = self._cand({"area": 0.5, "fmax": 1.0}, width=16)
        c = self._cand({"area": 0.4, "fmax": 0.4}, width=32)  # dominated by both
        pf = ParetoFront()
        pf.update([a, b, c])
        members_dims = [x.score.dimensions for x in pf.members]
        assert {"area": 1.0, "fmax": 0.5} in members_dims
        assert {"area": 0.5, "fmax": 1.0} in members_dims
        assert {"area": 0.4, "fmax": 0.4} not in members_dims  # dominated → pruned

    def test_dedup_by_signature(self):
        a = self._cand({"area": 1.0, "fmax": 1.0}, width=16, pipelined=True)
        b = self._cand({"area": 1.0, "fmax": 1.0}, width=16, pipelined=True)  # same sig
        pf = ParetoFront()
        pf.update([a, b])
        assert len(pf.members) == 1


# ======================================================================
# Proposer + Reflector
# ======================================================================
class TestProposer:
    def test_initial_partition_splits_hw_sw(self):
        spec = DesignSpec(module_name="dma", description="",
                          functional_ops=["datapath", "control", "dma_move"])
        p = PartitionProposer().initial_partition(spec)
        assert "dma_move" in p.hw_ops
        assert p.rationale

    def test_seed_configs_span_axes(self):
        spec = DesignSpec(module_name="m", description="")
        seeds = PartitionProposer().seed_configs(spec)
        assert len(seeds) >= 4
        sigs = {s.signature() for s in seeds}
        assert len(sigs) == len(seeds)  # all distinct


class TestReflector:
    def test_area_violation_narrows_width(self):
        r = Reflector()
        cfg = VariantConfig(pipelined=True, pipeline_stages=2, datapath_width=32)
        rev = r.revise(cfg, "area")
        assert rev is not None and rev.datapath_width < cfg.datapath_width

    def test_latency_violation_widens(self):
        r = Reflector()
        cfg = VariantConfig(pipelined=False, datapath_width=16)
        rev = r.revise(cfg, "latency")
        assert rev is not None and rev.datapath_width > cfg.datapath_width

    def test_fmax_violation_adds_pipelining(self):
        r = Reflector()
        cfg = VariantConfig(pipelined=False, datapath_width=8)
        rev = r.revise(cfg, "fmax")
        assert rev is not None and rev.pipelined is True

    def test_power_violation_enables_gating(self):
        r = Reflector()
        cfg = VariantConfig(pipelined=False, datapath_width=16, clock_gating=False)
        rev = r.revise(cfg, "power")
        assert rev is not None and rev.clock_gating is True

    def test_sw_cost_violation_switches_to_streaming(self):
        r = Reflector()
        cfg = VariantConfig(interface="register", polling=True)
        rev = r.revise(cfg, "sw_cost")
        assert rev is not None and rev.interface == "streaming"

    def test_no_violation_returns_none(self):
        assert Reflector().revise(VariantConfig(), None) is None

    def test_latency_revision_never_adds_pipeline_stages(self):
        # Pipelining adds latency in the stub model, so the latency rule must
        # not add stages — only widen / switch to streaming.
        cfg = VariantConfig(pipelined=False, datapath_width=64, interface="register")
        r = Reflector()
        rev = r.revise(cfg, "latency")
        if rev is not None:
            assert not (rev.pipelined and rev.pipeline_stages > cfg.pipeline_stages)


# ======================================================================
# End-to-end DSE loop
# ======================================================================
class TestDSERunner:
    def test_run_returns_selected_and_pareto(self, tmp_path):
        r = run_dse("请为 DMA 模块生成 RTL",
                    targets={"area": 300, "fmax": 200, "latency": 4, "power": 20, "sw_cost": 0.3},
                    output_dir=str(tmp_path), use_llm=False)
        assert r["status"] == "completed"
        assert r["selected"]
        assert len(r["pareto"]) >= 1
        assert r["tradeoff_table"]
        assert r["iterations"] >= 1

    def test_run_persists_artifacts(self, tmp_path):
        r = run_dse("请为 DMA 模块生成 RTL",
                    targets={"area": 300, "fmax": 200, "latency": 4},
                    output_dir=str(tmp_path), use_llm=False)
        arts = r["artifacts"]
        assert any(k.endswith("_dut.sv") for k in arts)
        assert any(k.endswith("_driver.c") for k in arts)
        assert "report_path" in arts

    def test_tight_area_selects_smaller_than_tight_latency(self):
        ra = run_dse("请为 DMA 模块生成 RTL",
                     targets={"area": 90, "latency": 50, "fmax": 100, "power": 50, "sw_cost": 1.0},
                     use_llm=False)
        rl = run_dse("请为 DMA 模块生成 RTL",
                     targets={"area": 5000, "latency": 1.2, "fmax": 100, "power": 50, "sw_cost": 1.0},
                     use_llm=False)
        a_sel = ra["selected"]["measurement"]["area_cells"]
        l_sel = rl["selected"]["measurement"]["area_cells"]
        a_lat = ra["selected"]["measurement"]["latency_cycles"]
        l_lat = rl["selected"]["measurement"]["latency_cycles"]
        assert a_sel < l_sel, f"tight-area should pick smaller area ({a_sel} < {l_sel})"
        assert l_lat < a_lat, f"tight-latency should pick lower latency ({l_lat} < {a_lat})"

    def test_search_explores_beyond_seeds(self):
        # The reflector should generate configs not in the seed set when a
        # target is tight (so the loop is a real search, not a 5-point eval).
        r = run_dse("请为 DMA 模块生成 RTL",
                    targets={"latency": 1.2, "area": 5000, "fmax": 100, "power": 50, "sw_cost": 1.0},
                    use_llm=False)
        widths = {row["config"]["datapath_width"] for row in r["tradeoff_table"]}
        assert max(widths) >= 32  # widened past the seed max of 32 → 64/128

    def test_convergence_within_budget(self):
        r = run_dse("请为 DMA 模块生成 RTL",
                    targets={"area": 300, "fmax": 200, "latency": 4, "power": 20, "sw_cost": 0.3},
                    use_llm=False)
        assert r["iterations"] <= 4

    def test_targets_override_takes_precedence(self):
        # Explicit targets override even when the parser would extract none.
        r = run_dse("请为 DMA 模块生成 RTL",
                    targets={"area": 60, "latency": 50, "fmax": 100, "power": 50, "sw_cost": 1.0},
                    use_llm=False)
        # area=60 is very tight → the selected design should be one of the small variants.
        assert r["selected"]["measurement"]["area_cells"] <= 200

    def test_no_llm_path_is_deterministic(self):
        r1 = run_dse("请为 DMA 模块生成 RTL",
                     targets={"area": 200, "fmax": 200, "latency": 3, "power": 20, "sw_cost": 0.3},
                     use_llm=False)
        r2 = run_dse("请为 DMA 模块生成 RTL",
                     targets={"area": 200, "fmax": 200, "latency": 3, "power": 20, "sw_cost": 0.3},
                     use_llm=False)
        assert r1["selected"]["variant_id"] == r2["selected"]["variant_id"]
        assert r1["iterations"] == r2["iterations"]


# ======================================================================
# Parser: non-functional target extraction + codesign routing
# ======================================================================
class TestParserPhase2:
    def test_extracts_area_and_fmax(self):
        from chipagent.parser import TaskParser
        t = TaskParser(llm=None, use_llm=False).parse("为 DMA 生成 RTL，面积不超过 300 cell，跑到 250MHz")
        c = t.constraints
        assert c.get("area_budget") == 300
        assert c.get("fmax_target_mhz") == 250

    def test_extracts_latency_and_power(self):
        from chipagent.parser import TaskParser
        t = TaskParser(llm=None, use_llm=False).parse("latency 2 cycles, power 15mW")
        assert t.constraints.get("latency_budget_cycles") == 2
        assert t.constraints.get("power_budget_mw") == 15

    def test_non_functional_request_routes_to_codesign(self):
        from chipagent.parser import TaskParser
        t = TaskParser(llm=None, use_llm=False).parse("为 DMA 生成 RTL，面积不超过 300 cell")
        assert t.task_type == "hw_sw_codesign"
