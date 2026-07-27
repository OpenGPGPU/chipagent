/* === HEADER {{module_name_upper}}_HAL_H === */
#ifndef {{module_name_upper}}_HAL_H
#define {{module_name_upper}}_HAL_H

#include <stdint.h>
#include "{{module_name}}_regs.h"

struct {{module_name}}_hal {
    uintptr_t base;
};

void {{module_name}}_hal_init(struct {{module_name}}_hal *h, uintptr_t base);
void {{module_name}}_hal_start(struct {{module_name}}_hal *h);
void {{module_name}}_hal_stop(struct {{module_name}}_hal *h);
int  {{module_name}}_hal_is_busy(struct {{module_name}}_hal *h);
uint32_t {{module_name}}_hal_read_status(struct {{module_name}}_hal *h);

#endif /* {{module_name_upper}}_HAL_H */
/* === END HEADER === */

/*
 * {{module_name}}_hal.c — Hardware Abstraction Layer for {{module_name}}_reg_top
 *
 * Wraps the {{module_name}}_regs.h defines with high-level helpers so
 * application code never touches raw MMIO. Portable C, no kernel headers.
 */

static inline void hal_write32(uintptr_t base, uint32_t off, uint32_t val)
{
    *((volatile uint32_t *)(base + off)) = val;
}

static inline uint32_t hal_read32(uintptr_t base, uint32_t off)
{
    return *((volatile uint32_t *)(base + off));
}

void {{module_name}}_hal_init(struct {{module_name}}_hal *h, uintptr_t base)
{
    h->base = base;
    /* Reset control: clear CTRL. */
    hal_write32(h->base, CTRL_ADDR, 0u);
}

void {{module_name}}_hal_start(struct {{module_name}}_hal *h)
{
    hal_write32(h->base, CTRL_ADDR, CTRL_START_MASK);
}

void {{module_name}}_hal_stop(struct {{module_name}}_hal *h)
{
    hal_write32(h->base, CTRL_ADDR, 0u);
}

int {{module_name}}_hal_is_busy(struct {{module_name}}_hal *h)
{
    return (int)(hal_read32(h->base, STATUS_ADDR) & STATUS_BUSY_MASK);
}

uint32_t {{module_name}}_hal_read_status(struct {{module_name}}_hal *h)
{
    return hal_read32(h->base, STATUS_ADDR);
}
