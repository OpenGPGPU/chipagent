# Lint rules (mandatory)

1. `module <module>_tb ... endmodule` balanced.
2. Balanced parentheses.
3. The testbench MUST print `PASS` (literal) on success and `FAIL` otherwise.
4. MUST call `$finish`.
5. MUST instantiate the DUT as `<module>_reg_top` with matching ports.
6. Combinable with `iverilog -g2012` against the RTL register block.
