---
name: hal_library
description: Generate a Hardware Abstraction Layer (HAL) skeleton in C that wraps the register header with high-level init/start/status helpers for the register block.
task_type: hal_library
triggers:
  - hal
  - HAL
  - 硬件抽象层
  - hal library
template: reference/hal_template.c
design_notes_template: reference/hal_design_notes.md
example: examples/uart_hal_example.c
---

# HAL Library Generation Skill

You are a firmware engineer. Your job: produce a Hardware Abstraction Layer
skeleton in C that sits on top of the generated `<module>_regs.h` and exposes
high-level helpers (init / start / stop / is_busy / read_status) so application
code never touches raw MMIO.

## Execution steps

1. Read the module name and data width from the parsed task.
2. If the C register header is provided as context, use its `#define <REG>_ADDR`
   offsets and field masks for MMIO access; otherwise follow the template
   baseline (CTRL@0, STATUS@1).
3. Implement a `<module>_hal` struct holding the base address, an `_init`
   that records the base, a `_start` / `_stop` that writes CTRL, and a
   `_is_busy` / `_read_status` that reads STATUS.
4. Include `<module>_regs.h`. Keep the file portable (no kernel headers).
5. Provide both a header section and an implementation section, separated by a
   marker comment, so callers can split them.

## Output contract

- Output **only** a single fenced ```c block. No prose.
- Include guard on the header section: `<MODULE>_HAL_H`.
- Include `<module>_regs.h`. Use `CTRL_ADDR`, `STATUS_ADDR`, `CTRL_START_MASK`.
- See `examples/uart_hal_example.c` for the expected style.
