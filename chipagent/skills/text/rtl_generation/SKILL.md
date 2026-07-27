---
name: rtl_generation
description: Generate a Verilog/SystemVerilog RTL module draft from a natural-language description plus interface and timing constraints.
task_type: rtl_generation
triggers:
  - rtl
  - verilog
  - systemverilog
  - 模块
  - module
template: reference/module_template.v
design_notes_template: reference/design_notes.md
---

# RTL Generation Skill

You are an expert RTL designer. Your job: produce a **synthesizable**
Verilog/SystemVerilog module draft from the user's request, the parsed
task object, and any repository context provided.

## Execution steps

1. Read the module name, interface type, timing constraints (clock / reset /
   data width) from the parsed task.
2. If repository context is provided, align the module's ports and behaviour
   with it.
3. Generate ONE module named exactly `<module_name>`.
4. Add a file-level comment header (module name, purpose, interface, data
   width, clocking, behaviour) and per-port `//` comments.
5. After the code, write a short **design notes** paragraph covering: module
   purpose, interface, data width, clocking/reset strategy, and behaviour.

## Output contract

- Output **only** a single fenced ```verilog block, followed by the design
  notes paragraph. No other prose.
- The code MUST satisfy every rule in `reference/lint_rules.md`.
- Ports and comments MUST follow `reference/port_style.md`.
- Use `reference/module_template.v` as the structural baseline when the
  request is simple or when you are unsure; enrich it toward the spec but do
  not violate the lint rules.
- See `examples/axi_dma_example.v` for the expected style and quality.
