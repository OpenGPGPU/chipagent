# HAL Library Lint Rules

Structural rules for a generated HAL skeleton:

1. The header section must be wrapped in an include guard `<MODULE>_HAL_H`.
2. The header must `#include "<module>_regs.h"`.
3. MMIO writes/reads must go through the offset/mask defines from the register
   header (`CTRL_ADDR`, `STATUS_ADDR`, `CTRL_START_MASK`, `STATUS_BUSY_MASK`).
4. Brace balance must hold after comment stripping.
5. The implementation must define every function declared in the header.
