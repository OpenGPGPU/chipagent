# 设计说明 — {{module_name}} 寄存器头文件

- 文件: {{module_name}}_regs.h
- 用途: C 侧寄存器地址与字段位定义，与 RTL {{module_name}}_reg_top 的 localparam 一一对应
- 数据宽度: {{data_width}} 位
- 寄存器偏移: CTRL=0, STATUS=1
- 字段: CTRL.start(bit0), STATUS.busy(bit0)
- 约定: #define <REG>_ADDR <offset>，寄存器名不带模块前缀，确保与 RTL ADDR_<REG> 对齐
