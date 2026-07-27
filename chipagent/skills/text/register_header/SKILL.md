---
name: register_header
description: Generate a C register header (<module>_regs.h) with address defines and field bit masks from a register-block description, aligned to the RTL register block.
task_type: register_header
triggers:
  - 寄存器头文件
  - 头文件
  - register header
  - header
template: reference/header_template.h
design_notes_template: reference/header_design_notes.md
example: examples/uart_regs_example.h
---

# Register Header Generation Skill

You are an expert in register-block software interfaces. Your job: produce a C
header (`<module>_regs.h`) whose address defines and field bit masks match the
RTL register block 1:1, so firmware can drive the hardware without mismatch.

## Execution steps

1. Read the module name and data width from the parsed task.
2. If the RTL register block code is provided as context, extract its
   `localparam ADDR_<NAME> = <offset>` declarations and field definitions and
   mirror them exactly in the header.
3. Otherwise, follow the template baseline: CTRL @ offset 0, STATUS @ offset 1.
4. Emit one `#define <REG>_ADDR <offset>` per register and `#define <REG>_<FIELD>_MASK`/`_SHIFT` per known field.
5. Wrap in an include guard named after the module.

## Output contract

- Output **only** a single fenced ```c block. No prose.
- The `#define <REG>_ADDR <offset>` names MUST match the RTL `ADDR_<REG>`
  localparams (the alignment checker compares them). Do NOT add a module prefix
  to the register name in the `_ADDR` define.
- Use the template `reference/header_template.h` as the structural baseline.
- See `examples/uart_regs_example.h` for the expected style.
