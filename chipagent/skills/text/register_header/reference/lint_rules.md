# Lint rules (mandatory)

1. Include guard: `#ifndef/<module>_REGS_H ... #endif` wraps the whole file.
2. One `#define <REG>_ADDR <offset>` per register; names match RTL `ADDR_<REG>`.
3. No module prefix on the register name in `_ADDR` defines (alignment checker
   compares `<REG>_ADDR` to RTL `ADDR_<REG>` directly).
4. Field masks use `(1u << SHIFT)` form; every field has `_SHIFT` and `_MASK`.
5. No trailing syntax errors; every `#define` is on its own line.
