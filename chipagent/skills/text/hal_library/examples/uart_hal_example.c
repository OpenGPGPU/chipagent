/* === HEADER UART_HAL_H === */
#ifndef UART_HAL_H
#define UART_HAL_H

#include <stdint.h>
#include "uart_regs.h"

struct uart_hal {
    uintptr_t base;
};

void uart_hal_init(struct uart_hal *h, uintptr_t base);
void uart_hal_start(struct uart_hal *h);
void uart_hal_stop(struct uart_hal *h);
int  uart_hal_is_busy(struct uart_hal *h);
uint32_t uart_hal_read_status(struct uart_hal *h);

#endif /* UART_HAL_H */
/* === END HEADER === */

/*
 * uart_hal.c — Hardware Abstraction Layer for uart_reg_top
 */

static inline void hal_write32(uintptr_t base, uint32_t off, uint32_t val)
{
    *((volatile uint32_t *)(base + off)) = val;
}

static inline uint32_t hal_read32(uintptr_t base, uint32_t off)
{
    return *((volatile uint32_t *)(base + off));
}

void uart_hal_init(struct uart_hal *h, uintptr_t base)
{
    h->base = base;
    hal_write32(h->base, CTRL_ADDR, 0u);
}

void uart_hal_start(struct uart_hal *h)
{
    hal_write32(h->base, CTRL_ADDR, CTRL_START_MASK);
}

void uart_hal_stop(struct uart_hal *h)
{
    hal_write32(h->base, CTRL_ADDR, 0u);
}

int uart_hal_is_busy(struct uart_hal *h)
{
    return (int)(hal_read32(h->base, STATUS_ADDR) & STATUS_BUSY_MASK);
}

uint32_t uart_hal_read_status(struct uart_hal *h)
{
    return hal_read32(h->base, STATUS_ADDR);
}
