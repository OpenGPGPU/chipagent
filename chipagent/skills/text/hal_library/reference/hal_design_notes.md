# 设计说明 — {{module_name}} HAL 库

- 文件: {{module_name}}_hal（头 + 实现，可按 marker 拆分）
- 依赖: {{module_name}}_regs.h（CTRL_ADDR / STATUS_ADDR / CTRL_START_MASK / STATUS_BUSY_MASK）
- 层级: 硬件抽象层，封装 MMIO 读写，对上层提供 init/start/stop/is_busy/read_status
- 可移植: 纯 C，仅依赖 stdint.h，不引入内核头
- 说明: 骨架级，未含中断处理 / 超时 / 错误重试，需对照实际 SoC 集成细化
