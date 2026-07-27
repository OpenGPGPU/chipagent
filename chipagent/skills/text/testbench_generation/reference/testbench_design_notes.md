# 设计说明 — {{module_name}} testbench

- 模块: {{module_name}}_tb，实例化 DUT {{module_name}}_reg_top
- 时钟: 100MHz (周期 10ns)，复位上电后释放
- 测试序列: 写 CTRL=1 → 检查 ctrl_start 拉高 → 回读 CTRL 校验
- 判定: 全部通过打印 PASS，否则打印 FAIL，最后 $finish
- 数据宽度: {{data_width}} 位
