# 设计说明 — {{module_name}} 寄存器块

- 模块: {{module_name}}_reg_top
- 类型: 内存映射寄存器块（CPU 读写译码）
- 数据宽度: {{data_width}} 位
- 时序: rising-edge clocked with asynchronous active-low reset (rst_n)
- 寄存器映射:
  - CTRL   @ 偏移 0 — 控制寄存器，bit0=start
  - STATUS @ 偏移 1 — 状态寄存器，硬件可读
- 接口: clk/rst_n/reg_write/reg_addr[2:0]/reg_wdata/reg_rdata + ctrl_start/status_q
