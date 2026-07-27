---
name: testbench_generation
description: Generate a SystemVerilog testbench that instantiates the register-block DUT, drives stimulus, and prints PASS/FAIL for simulation.
task_type: testbench_generation
triggers:
  - testbench
  - 测试平台
  - 测试
  - 仿真测试
template: reference/testbench_template.sv
design_notes_template: reference/testbench_design_notes.md
example: examples/uart_tb_example.sv
---

# Testbench Generation Skill

You are a verification engineer. Your job: produce a SystemVerilog testbench
that instantiates the register-block DUT, exercises a write/read-back, and
prints `PASS` or `FAIL` so the simulation tool can judge the run.

## Execution steps

1. Read the module name and data width from the parsed task.
2. If the RTL register-block code is provided as context, instantiate its top
   module (`<module>_reg_top`) and drive its actual ports.
3. Otherwise follow the template baseline (CTRL@0, STATUS@1).
4. Drive clk/rst_n, write CTRL=1, check `ctrl_start` asserted, read CTRL back
   and check the value.
5. Print `PASS` if all checks pass, else `FAIL`. Always `$finish`.

## Output contract

- Output **only** a single fenced ```systemverilog block. No prose.
- Top module name: `<module>_tb`.
- MUST compile with `iverilog -g2012` alongside the RTL.
- The testbench MUST print `PASS` on success (the sim tool greps for it).
- See `examples/uart_tb_example.sv` for the expected style.
