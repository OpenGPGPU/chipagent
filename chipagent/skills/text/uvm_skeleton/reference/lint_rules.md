# UVM Skeleton Lint Rules

Structural rules the offline lint gate enforces on a generated UVM skeleton:

1. `package ... endpackage` must be balanced.
2. `class ... endclass` pairs must be balanced (one per declared class).
3. `module ... endmodule` for the top `<module>_uvm_tb` must be balanced.
4. `interface ... endinterface` must be balanced.
5. Parentheses must balance after comment stripping.
6. The top `initial` block must `$finish` so the simulation terminates.
7. Package name must be `<module>_uvm_pkg`; top module `<module>_uvm_tb`.
