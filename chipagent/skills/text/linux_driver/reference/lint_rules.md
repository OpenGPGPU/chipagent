# Lint rules (mandatory)

1. File header comment with module name and depends-on header.
2. `#include "<module>_regs.h"` present (uses the generated header defines).
3. `*_read_reg` / `*_write_reg` MMIO helpers using `CTRL_ADDR` / `STATUS_ADDR`.
4. `probe`/`remove` + `module_platform_driver` skeleton complete.
5. `MODULE_LICENSE`, `MODULE_AUTHOR`, `MODULE_DESCRIPTION` present.
6. Balanced braces and parentheses.
