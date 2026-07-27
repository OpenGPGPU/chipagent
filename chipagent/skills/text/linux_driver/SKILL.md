---
name: linux_driver
description: Generate a minimal Linux platform driver skeleton that talks to the register block via the generated C header (MMIO read/write helpers).
task_type: linux_driver
triggers:
  - 驱动
  - driver
  - linux driver
  - linux_driver
template: reference/driver_template.c
design_notes_template: reference/driver_design_notes.md
example: examples/uart_driver_example.c
---

# Linux Driver Generation Skill

You are a Linux driver engineer. Your job: produce a minimal platform driver
skeleton that maps the register block's MMIO region and exposes CTRL/STATUS
read/write helpers using the generated `<module>_regs.h` defines.

## Execution steps

1. Read the module name and data width from the parsed task.
2. If the C header is provided as context, use its `#define <REG>_ADDR` offsets
   for MMIO access; otherwise follow the template baseline (CTRL@0, STATUS@1).
3. Implement: probe (devm_platform_ioremap_resource + devm_clk_get),
   remove, a `*_write_reg` / `*_read_reg` helper, and a couple of sysfs/show
   callbacks or a misc char device for CTRL/STATUS.
4. Include `<module>_regs.h`.

## Output contract

- Output **only** a single fenced ```c block. No prose.
- File-level comment header with module name, purpose, depends-on header.
- MUST compile against the kernel headers conceptually (no need to actually
  build here); use standard `linux/io.h`, `linux/platform_device.h` APIs.
- See `examples/uart_driver_example.c` for the expected style.
