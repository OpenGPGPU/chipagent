# ChipAgent 使用示例（用户视角）

> ChipAgent 有两种主要使用方式：
> 1. **Claude Code MCP 集成**（推荐）：接入 Claude Code 后用自然语言驱动全链路
> 2. **CLI 直接使用**：终端里用命令行跑单个任务

---

## A. Claude Code MCP 集成（推荐）

### 0. 前置

```bash
pip install -e .            # 装 ChipAgent
bash scripts/setup_eda_env.sh --docker
bash scripts/setup_eda_env.sh --smoke
```

仓库根已有 `.mcp.json`，Claude Code 打开项目后自动发现 ChipAgent MCP Server。

### 1. 设计探索（DSE）

在 Claude Code 里直接对话：

```
你：给 DMA 设计一个 RTL，面积别超 300 cell，跑 250MHz
Claude：（调 chipagent_run_dse）
      → 返回 tradeoff 表 + Pareto 前沿 + 选定设计
      → 跟你讨论选哪个变体，解释为什么选 v6

你：把上次的方案再改小一点
Claude：（收紧 area_budget 重跑 DSE）
```

### 2. 完整设计流程

```
你：给 UART 做一套完整设计，从 RTL 到驱动
Claude：（生成 RTL + 寄存器头文件 + HAL + testbench + Linux 驱动）
      → 调用 chipagent_lint 检查语法
      → 调用 chipagent_run_simulation 跑仿真
      → 调用 chipagent_check_register_alignment 检查对齐
      → 根据错误迭代优化
      → 返回全套产物 + 验证报告
```

### 3. 单步操作

```
你：跑一下这个 testbench 看仿真过不过
Claude：（调 chipagent_run_simulation）

你：这份 RTL 综合面积大概多少？
Claude：（调 chipagent_elaborate）

你：寄存器和头文件对得上吗？
Claude：（调 chipagent_check_register_alignment）

你：驱动和 RTL 的接口匹配吗？
Claude：（调 chipagent_check_sw_hw_interface）
```

### 4. 任务审批

```
你：提交一个 UART 设计任务，需要我审批才能落地
Claude：（调 chipagent_task_submit with require_approval=true）
      → 产物暂存，等你审批

你：批准 task-20250713-001
Claude：（调 chipagent_task_approve，产物写入 generated/）
```

### 5. 查看可用能力

```
你：chipagent 能做哪些事？
Claude：（调 chipagent_list_skills 和 chipagent_list_tools）
      → 列出 Skill + Python-backed 工具，并标出 physical/heavy 工具
```

> 详见 [CLAUDE_CODE_INTEGRATION.md](../CLAUDE_CODE_INTEGRATION.md) 完整工具清单。

### 6. 一键跑通 EDA + ASAP7 物理流

```
你：用内置 tiny_counter 跑一次完整 ChipAgent 自检，包含 ASAP7 physical
Claude：（调 chipagent_run_example_flow(run_physical=true)）
      → 返回 simulation/synthesis/formality/physical summary
      → 生成 ASAP7 DEF/GDS/ODB 和 ORFS 日志
```

直接用 Python 调 MCP wrapper 也可以：

```bash
python - <<'PY'
import json
from chipagent.mcp import chipagent_run_example_flow

result = json.loads(chipagent_run_example_flow(run_physical=True))
print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
PY
```

典型输出目录：

```text
generated/examples/tiny_counter/
  tiny_counter_flow_report.json
  tiny_counter_flow_summary.html
  physical_asap7/orfs-work/results/base/6_final.def
  physical_asap7/orfs-work/results/base/6_final.gds
  physical_asap7/orfs_run.log
```

如果 physical 失败，`summary.physical.diagnosis` 会给出失败阶段、root cause、suggested fix 和 evidence log lines。
如果想快速浏览结果，直接打开 `tiny_counter_flow_summary.html`，里面会集中展示 QoR、artifact 链接和 ORFS 图片。

ASAP7 physical 默认启用缓存。同一份 RTL/参数第二次运行会复用已有 DEF/GDS/log，summary 中 `physical.cached` 会显示是否命中。需要强制重跑时使用 `physical_clean=True` 或单独调用 `chipagent_run_physical_flow_asap7(..., clean=True)`。

### 7. SystemVerilog 参数 DSE

对已有参数化 RTL，可以让 ChipAgent 展开参数网格，逐个跑现有 flow 并排序：

```bash
python - <<'PY'
import json
from chipagent.mcp import chipagent_run_sv_parameter_dse

rtl = '''
module param_adder #(parameter WIDTH = 8)(
  input [WIDTH-1:0] a,
  input [WIDTH-1:0] b,
  output [WIDTH:0] sum
);
  assign sum = a + b;
endmodule
'''

result = json.loads(chipagent_run_sv_parameter_dse(
    reg_code=rtl,
    module_name="param_adder",
    parameters={"WIDTH": [4, 8, 16]},
    output_dir="generated/sv_dse/param_adder",
))
print(json.dumps(result["best"], indent=2, ensure_ascii=False))
PY
```

需要真实 ASAP7 QoR 时打开 `run_physical=True`，但会更慢：

```python
chipagent_run_sv_parameter_dse(..., run_physical=True)
```

---

## B. CLI 直接使用

### 0. 前置

```bash
pip install -e .            # 装 ChipAgent
bash scripts/setup_eda_env.sh --docker
bash scripts/setup_eda_env.sh --smoke
```

确认 `chipagent` 命令可用：

```bash
chipagent --help
# usage: chipagent [-h] [--context CONTEXT_PATH] [--context-dir CONTEXT_DIR]
#                  [--output-dir OUTPUT_DIR] [--no-llm] request
```

### 1. 一句话生成 RTL

```bash
chipagent "请为 AXI DMA 模块生成一个简单的 RTL 模块，带时钟和复位，数据宽度 16-bit" \
  --output-dir ./generated
```

ChipAgent 把这句话解析成结构化任务：`task_type=rtl_generation`、`module_name=axi_dma`、
`constraints={clock, reset, data_width:16, interface:axi}`，然后走
`解析 → 生成 → 校验 → 回写仓库` 四步。

### 2. 你拿回什么

终端打印一份 JSON 结果，里面包含状态、生成的代码、校验结果和执行日志：

```json
{
  "status": "completed",
  "task_type": "rtl_generation",
  "output": {
    "code": "module axi_dma (...);  always_ff @(posedge clk or negedge rst_n) ... endmodule",
    "checks": { "lint": "passed", "syntax": "passed", "issues": [] },
    "design_notes": "## 设计说明 — axi_dma  ..."
  },
  "logs": [
    { "step": "parse_request",  "status": "completed", ... },
    { "step": "generate_rtl",   "status": "completed", ... },
    { "step": "validate",       "status": "completed", ... },
    { "step": "persist_output", "status": "completed", ... }
  ],
  "artifacts": { "code_path": "...", "design_path": "...", "report_path": "...", "log_path": "..." }
}
```

落盘的产物（在 `--output-dir` 指定目录下）：

```
generated/
  axi_dma.v               # 生成的 Verilog 代码（含文件头注释 + 端口注释）
  axi_dma.md              # 设计说明（模块类型/数据宽度/时序/行为/端口）
  workflow_report.json    # 任务 + 校验结果 + 完整执行日志
logs/
  axi_dma.json            # 可追溯执行日志
```

### 3. 想要更高质量？接 LLM

默认就走 LLM（环境配了 OpenAI/Anthropic 兼容端点时就用）。LLM 会按
Skill 里的指令 + lint 规则生成更贴近规格的代码，并自动过结构自检；
不过校验会回退到模板草稿，保证总能返回一份可用的 RTL。

```bash
chipagent "请为 AXI DMA 模块生成一个简单的 RTL 模块，带 AXI4-Stream 接口" \
  --output-dir ./generated
```

纯模板、不联网、确定性输出：

```bash
chipagent "..." --no-llm --output-dir ./generated
```

### 4. 带仓库上下文生成

你仓库里已有规格文档，让 ChipAgent 读它再生成：

```bash
chipagent "请根据规格生成 AXI DMA RTL" --context-dir ./spec --output-dir ./generated
```

`--context-dir` 会递归读取 `spec/` 下的 `.md/.v/.sv/.json/.yaml` 作为上下文，
喂给生成 Skill。

### 5. 换个任务类型：寄存器块

ChipAgent 不只生成 RTL 模块，还能生成寄存器块（第二个 Skill）：

```bash
chipagent "请为 UART 寄存器块生成定义，数据宽度 32-bit，包含 CTRL 和 STATUS" \
  --output-dir ./generated
```

解析为 `task_type=reg_definition`，产出 `uart_reg_top` SV 模块（带 CPU 读写译码、
地址映射、CTRL/STATUS 寄存器），同样过 lint、落设计说明。

### 6. 跑 DSE（设计空间探索）

```bash
python -m chipagent.workflow dse "设计一个 AXI DMA" \
  --targets area=300,fmax=250
```

返回 tradeoff 表 + Pareto 前沿 + 选定设计 + 分区理由。

### 7. 启动 MCP 服务

```bash
python -m chipagent.mcp
```

交互式会话由 Claude Code 或其他 MCP host 提供。历史
`python -m chipagent.workflow chat` 入口已经废弃。

### 9. 要 durable / 可恢复？走 Temporal

长任务、要状态可追溯可恢复时，切到 Temporal 路径（需 Temporal server + worker）：

```bash
# 终端 A：起 dev server
temporal server start-dev

# 终端 B：起 worker（轮询 chipagent-phase1 队列）
python -m chipagent.temporal_runtime worker

# 终端 C：提交工作流（CHIPAGENT_USE_TEMPORAL=1 切换到 Temporal 路径）
CHIPAGENT_USE_TEMPORAL=1 chipagent "请为 AXI DMA 模块生成 RTL" --output-dir ./generated
```

返回结果形状和默认 LangGraph 路径完全一样，但这次状态/重试/恢复由 Temporal server
持有——`temporal workflow list` 能看到这次 `ChipAgentWorkflow` 的执行记录。

---

## 速查

| 想做的事 | 方式 |
|---|---|
| Claude Code 里对话设计 | 打开项目，Claude Code 自动发现 MCP Server |
| 一句话生成 RTL | `chipagent "<需求>" --output-dir ./generated` |
| 设计空间探索 | `python -m chipagent.workflow dse "..." --targets area=300,fmax=250` |
| 纯模板、不联网 | 加 `--no-llm` |
| 带仓库上下文 | 加 `--context-dir ./spec` |
| 生成寄存器块 | 描述里带"寄存器块定义" |
| 交互式多轮对话 | 在 Claude Code 或其他 MCP host 中连接 `python -m chipagent.mcp` |
| 走 Temporal（durable） | `CHIPAGENT_USE_TEMPORAL=1 chipagent ...`，先起 server+worker |
| 任务提交+审批 | Claude Code 对话 / `python -m chipagent.workflow task submit "..."` |
| 查看 MCP 工具清单 | `python -m chipagent.mcp --list` |
| 直接看产物 | `generated/<module>.v`、`<module>.md`、`workflow_report.json` |
