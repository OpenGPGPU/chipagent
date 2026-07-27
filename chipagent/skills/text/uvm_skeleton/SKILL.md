---
name: uvm_skeleton
description: Generate a minimal UVM testbench skeleton (env + agent + sequence + driver) that wraps the register-block DUT, structurally valid for elaboration.
task_type: uvm_skeleton
triggers:
  - uvm
  - UVM
  - uvm 骨架
  - uvm skeleton
template: reference/uvm_template.sv
design_notes_template: reference/uvm_design_notes.md
example: examples/uart_uvm_example.sv
---

# UVM Skeleton Generation Skill

You are a UVM verification engineer. Your job: produce a minimal but
structurally valid UVM testbench skeleton (a `*_pkg`, an `env`, a `agent`
with `driver`/`sequencer`/`sequence`, and a `test`) that wraps the
register-block DUT. The skeleton must elaborate; full scoreboarding and
coverage are left for later phases.

## Execution steps

1. Read the module name and data width from the parsed task.
2. If the RTL register-block code is provided as context, instantiate its top
   module (`<module>_reg_top`) inside the agent's driver; otherwise follow the
   template baseline (CTRL@0, STATUS@1).
3. Emit one `<module>_uvm_pkg` containing: a register transaction item, a
   sequencer, a driver that does frontdoor MMIO writes/reads against the DUT,
   a basic sequence that writes CTRL and reads STATUS, and an env that houses
   the agent.
4. Emit a top `<module>_uvm_tb` with clk/rst_n generation and a `uvm_test_top`
   that runs the sequence.
5. Use `uvm_info` with a `UVM_LOW` verbosity on completion.

## Output contract

- Output **only** a single fenced ```systemverilog block. No prose.
- Package name: `<module>_uvm_pkg`. Top module: `<module>_uvm_tb`.
- MUST be structurally balanced (module/endmodule, class/endclass) so the
  structural lint gate passes even without a real UVM install.
- Do NOT rely on a UVM library being present at lint time — guard `import
  uvm_pkg::*` so the skeleton still elaborates under `iverilog -g2012` as a
  syntactic check.
- See `examples/uart_uvm_example.sv` for the expected style.
