# 设计说明 — {{module_name}} Linux 驱动

- 文件: {{module_name}}_driver.c，依赖 {{module_name}}_regs.h
- 类型: Linux platform driver 骨架，misc char 设备暴露 CTRL/STATUS
- MMIO: probe 时 ioremap 寄存器区，启动时写 CTRL.start
- 接口: read 返回 STATUS，write 写 CTRL（4 字节）
- 兼容: "{{module_name}},reg-top" 设备树节点
- License: GPL
- 说明: 骨架级，未含完整错误处理与并发控制，需对照实际内核版本细化
