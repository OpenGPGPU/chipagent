# 设计说明 — {{module_name}}

- 模块类型: RTL 数据通路草稿（{{interface}} 接口）
- 数据宽度: {{data_width}} 位
- 时序: {{timing_desc}}
- 行为: 复位时清零输出；否则将 data_in 寄存到 data_out 并拉高 valid
- 端口: {{#if clock}}clk/{{/if}}{{#if reset}}rst_n/{{/if}}data_in[{{data_width}}]/data_out[{{data_width}}]/valid
