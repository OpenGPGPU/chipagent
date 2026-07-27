#ifndef UART_REGS_H
#define UART_REGS_H

/* Register offsets — mirror RTL localparam ADDR_<NAME> */
#define CTRL_ADDR   0
#define STATUS_ADDR 1

/* CTRL fields */
#define CTRL_START_SHIFT 0
#define CTRL_START_MASK  (1u << CTRL_START_SHIFT)

/* STATUS fields */
#define STATUS_BUSY_SHIFT 0
#define STATUS_BUSY_MASK  (1u << STATUS_BUSY_SHIFT)

#endif /* UART_REGS_H */
