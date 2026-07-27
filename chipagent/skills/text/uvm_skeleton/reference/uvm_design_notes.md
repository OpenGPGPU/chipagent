# 设计说明 — {{module_name}} UVM 骨架

- 文件: {{module_name}}_uvm_pkg + {{module_name}}_uvm_tb
- 类型: UVM 测试平台骨架（pkg/env/agent/driver/sequence/test）
- DUT: {{module_name}}_reg_top，数据宽度 {{data_width}} 位
- 骨架级：结构完整可 elaborate，未含完整 scoreboard / coverage / 寄存器抽象层
- 约定: 顶层 initial 打印 PASS 后 $finish，便于仿真工具判定
- 说明: 离线模板生成，UVM import 已做保护，需在真实 UVM 环境补全 sequence/driver 行为
