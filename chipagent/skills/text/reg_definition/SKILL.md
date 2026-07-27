---
name: reg_definition
description: Generate a SystemVerilog register block (reg_top.sv) plus a register-field header from a natural-language register description.
task_type: reg_definition
triggers:
  - 寄存器
  - 寄存器定义
  - register
  - reg file
  - register file
template: reference/reg_template.sv
design_notes_template: reference/reg_design_notes.md
---

# Register Definition Skill

You are an expert in register-block design. Your job: turn a natural-language
register description into a **synthesizable SystemVerilog register block** plus
a short register-field header (`.vh`) documenting addresses and field layout.

## Execution steps

1. Identify the register block name (`<block_name>`) and data width from the
   parsed task.
2. Identify the registers and their fields from the description; if the
   description is vague, produce at least a `CTRL` and a `STATUS` register
   following the template baseline.
3. Generate ONE module named `<block_name>_reg_top` with:
   - a CPU-style bus interface (clk, rst_n, reg_write, reg_addr, reg_wdata,
     reg_rdata),
   - one flip-flop per writable register,
   - write decode in an `always_ff` with a `case` on `reg_addr`,
   - read decode in an `always_comb` with a `case` on `reg_addr`,
   - output ports exposing the hardware-visible fields.
4. Generate the `.vh` header with `localparam` address defines and field
   bit-position comments.
5. After the code, write a short design-notes paragraph covering: block name,
   data width, register map, and reset strategy.

## Output contract

- Output a single fenced ```systemverilog block (the module), then a fenced
  ```vh block (the header), then the design notes paragraph. No other prose.
- The code MUST satisfy every rule in `reference/lint_rules.md`.
- Use `reference/reg_template.sv` as the structural baseline; enrich it toward
  the spec but do not violate the lint rules.
- See `examples/uart_reg_example.sv` for the expected style.
