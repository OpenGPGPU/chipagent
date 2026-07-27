"""Parametric RTL/driver variant builders for the DSE loop.

These are **parametric Python builders**, not text-skill templates. The skill
template engine (``chipagent/skills/loader.py``) has no loop construct, so
multi-stage pipelining, clock-gate cells, and streaming handshake ports are
cleaner to emit directly. Each ``build_variant_rtl(config, module)`` returns a
structurally-balanced SystemVerilog module whose shape depends on the config —
so the stub measurer and the structural lint gate both see real differences
between variants.

The builders intentionally produce small, illustrative modules: the DSE loop
measures *tradeoffs between microarchitecture choices*, not a full SoC. A real
production run would swap :func:`build_variant_rtl` for a richer generator
(or an LLM-directed one) without changing the loop.
"""
from __future__ import annotations

from typing import List

from .models import VariantConfig


def _width_m1(w: int) -> int:
    return max(0, w - 1)


def build_variant_rtl(config: VariantConfig, module: str) -> str:
    """Emit a structurally-balanced SV module for the given variant config."""
    m = module or "block"
    w = max(1, config.datapath_width)
    wm1 = _width_m1(w)
    stages = max(1, config.pipeline_stages) if config.pipelined else 0

    # ---- Ports -----------------------------------------------------------
    ports: List[str] = ["input logic clk", "input logic rst_n"]
    if config.clock_gating:
        ports.append("input logic clk_en")
    if config.interface == "streaming":
        ports += [
            f"input logic [{wm1}:0] data_in",
            "input logic valid_in",
            "output logic ready_out",
            f"output logic [{wm1}:0] data_out",
            "output logic valid_out",
            "input logic ready_in",
        ]
    else:  # register interface (MMIO)
        ports += [
            "input logic reg_write",
            "input logic [2:0] reg_addr",
            f"input logic [{wm1}:0] reg_wdata",
            f"output logic [{wm1}:0] reg_rdata",
            f"output logic [{wm1}:0] status_q",
        ]

    # ---- Body ------------------------------------------------------------
    body: List[str] = []
    # Clock gate cell.
    if config.clock_gating:
        body.append("    logic clk_g;  // clock-gate cell")
        body.append("    always_latch begin")
        body.append("        if (!clk) clk_g <= clk_en;")
        body.append("    end")
        body.append("    assign clk_g = clk & clk_g;  // gated clock (illustrative)")
        clock_expr = "clk_g"
    else:
        clock_expr = "clk"

    # Pipeline registers (each stage adds a register → area + Fmax).
    if config.pipelined and stages > 0:
        for i in range(stages):
            body.append(f"    logic [{wm1}:0] pipe_q{i};")
        body.append(f"    always_ff @(posedge {clock_expr} or negedge rst_n) begin")
        body.append("        if (!rst_n) begin")
        for i in range(stages):
            body.append(f"            pipe_q{i} <= '0;")
        body.append("        end else begin")
        if config.interface == "streaming":
            body.append("            pipe_q0 <= data_in;")
            for i in range(1, stages):
                body.append(f"            pipe_q{i} <= pipe_q{i-1};")
            body.append(f"            data_out <= pipe_q{stages-1};")
        else:
            body.append("            pipe_q0 <= reg_wdata;")
            for i in range(1, stages):
                body.append(f"            pipe_q{i} <= pipe_q{i-1};")
            body.append("            reg_rdata <= pipe_q{stages-1};")
        body.append("        end")
        body.append("    end")
        if config.interface == "streaming":
            body.append("    assign valid_out = valid_in & ready_in;")
            body.append("    assign ready_out = ready_in;")
    else:
        # Combinational datapath (no registers → low area, low Fmax).
        if config.interface == "streaming":
            body.append("    assign data_out = data_in;")
            body.append("    assign valid_out = valid_in & ready_in;")
            body.append("    assign ready_out = ready_in;")
        else:
            body.append("    always_ff @(posedge clk or negedge rst_n) begin")
            body.append("        if (!rst_n) reg_rdata <= '0;")
            body.append("        else if (reg_write) reg_rdata <= reg_wdata;")
            body.append("    end")
        body.append("    assign status_q = '0;")

    # Streaming FIFO (buffer_depth adds storage → area, decouples latency).
    if config.interface == "streaming" and config.buffer_depth > 0:
        body.append(f"    // FIFO depth = {config.buffer_depth} (storage added)")
        body.append("    logic [15:0] fifo_cnt;")
        body.append("    always_ff @(posedge clk or negedge rst_n) begin")
        body.append("        if (!rst_n) fifo_cnt <= '0;")
        body.append("        else if (valid_in & ready_out) fifo_cnt <= fifo_cnt + 1'b1;")
        body.append("    end")

    port_list = ",\n    ".join(ports)
    body_text = "\n".join(body)
    return (
        f"`timescale 1ns / 1ps\n"
        f"// variant: pipelined={config.pipelined} stages={stages} width={w} "
        f"buffer={config.buffer_depth} gating={config.clock_gating} iface={config.interface}\n"
        f"module {m} (\n"
        f"    {port_list}\n"
        f");\n"
        f"{body_text}\n"
        f"endmodule\n"
    )


def build_variant_driver(config: VariantConfig, module: str) -> str:
    """Emit a C driver skeleton matching the variant's interface choice.

    The register-interface driver mirrors the MMIO style the existing
    ``linux_driver`` skill uses; the streaming driver shows a low-CPU
    interrupt-driven access pattern — which is what lowers ``sw_cost``.
    """
    m = module or "block"
    if config.interface == "streaming":
        return (
            f"/* {m}_driver.c — streaming driver for {m} (low CPU, interrupt-driven) */\n"
            f"#include <stdint.h>\n"
            f"struct {m}_ctx {{ uintptr_t base; }};\n"
            f"void {m}_isr(struct {m}_ctx *c) {{\n"
            f"    /* drain streaming fifo on interrupt; no polling loop */\n"
            f"    (void)c;\n"
            f"}}\n"
            f"void {m}_start(struct {m}_ctx *c) {{ (void)c; }}\n"
        )
    style = "polling" if config.polling else "interrupt"
    return (
        f"/* {m}_driver.c — register-MMIO driver ({style}) for {m} */\n"
        f"#include <stdint.h>\n"
        f"#include \"{m}_regs.h\"\n"
        f"struct {m}_ctx {{ uintptr_t base; }};\n"
        f"static inline void {m}_write_reg(struct {m}_ctx *c, uint32_t off, uint32_t v) {{\n"
        f"    *((volatile uint32_t *)(c->base + off)) = v;\n"
        f"}}\n"
        f"static inline uint32_t {m}_read_reg(struct {m}_ctx *c, uint32_t off) {{\n"
        f"    return *((volatile uint32_t *)(c->base + off));\n"
        f"}}\n"
        + (
            f"/* busy-wait polling loop — higher CPU cost */\n"
            f"void {m}_wait_ready(struct {m}_ctx *c) {{\n"
            f"    while (!({m}_read_reg(c, STATUS_ADDR) & STATUS_BUSY_MASK)) {{}}\n"
            f"}}\n"
            if config.polling
            else f"/* interrupt-driven — registers ISR, no busy-wait */\n"
            f"void {m}_register_isr(struct {m}_ctx *c) {{ (void)c; }}\n"
        )
    )
