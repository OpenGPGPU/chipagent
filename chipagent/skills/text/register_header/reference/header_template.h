#ifndef {{module_name_upper}}_REGS_H
#define {{module_name_upper}}_REGS_H

/*
 * Register definitions for {{module_name}}_reg_top.
 * Data width: {{data_width}} bits.
 * These defines mirror the RTL localparam ADDR_<NAME> offsets 1:1.
 */

/* Register offsets */
#define CTRL_ADDR   0
#define STATUS_ADDR 1

/* CTRL register fields (bit positions) */
#define CTRL_START_SHIFT 0
#define CTRL_START_MASK  (1u << CTRL_START_SHIFT)

/* STATUS register fields */
#define STATUS_BUSY_SHIFT  0
#define STATUS_BUSY_MASK   (1u << STATUS_BUSY_SHIFT)

#endif /* {{module_name_upper}}_REGS_H */
