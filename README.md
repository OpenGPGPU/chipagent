# ChipAgent

> **芯片设计领域的 EDA 工具 MCP 服务层** — 让 Claude Code 可以通过 MCP 协议调用真实的芯片设计工具。

ChipAgent 是一个 **EDA 工具的 MCP 封装**，让 LLM（如 Claude Code）可以通过 MCP 协议
调用真实的芯片设计工具（Yosys、OpenROAD、Verilator 等），实现芯片设计流程的自动化验证。

当前架构以 [ChipAgent_Architecture_v3.0.md](ChipAgent_Architecture_v3.0.md) 为准。旧版
“AI 芯片开发智能体系统”设计已经废弃。

**重要**: ChipAgent 不是"AI 芯片设计师"，而是"EDA 工具的服务层"。
- ✅ ChipAgent 负责：封装真实 EDA 工具、提供验证能力
- ✅ Claude 负责：理解需求、生成代码、迭代优化
- ❌ ChipAgent 不负责：复杂/生产级代码生成、架构设计决策

## 核心能力

| 能力域 | 说明 |
|---|---|
| **设计空间探索（DSE）** | 对候选变体做 PPA/SW-cost 评估、排序和 tradeoff 汇总；不作为复杂设计生成大脑 |
| **综合与物理设计** | Yosys 综合、ASAP7 OpenROAD Flow Scripts 到 DEF/GDS；单阶段 floorplan/place/CTS/route 工具在提供 PDK 输入时可用 |
| **验证仿真与波形分析** | 编译跑 testbench；流式分析 VCD/FST，输出 ready/valid 事务、ID 配对、stall 与协议违规证据 |
| **对齐检查** | RTL 寄存器偏移 vs C 头文件一致性、驱动接口契约 vs RTL |
| **PPA 分析** | 早期静态估算和目标检查；结果必须标记为 estimate，不能替代真实综合/时序/功耗工具 |
| **SystemVerilog 参数 DSE** | 对已有参数化 RTL 做参数网格探索，复用仿真/综合/可选 ASAP7 physical，并按 QoR 排序 |
| **知识库** | TF-IDF 检索设计知识、代码示例、架构咨询 |
| **多 Agent 协作** | 历史/实验能力；当前优先级低于可信 EDA 工具封装 |
| **任务面板** | 提交/审批/回滚，关键节点人工确认 |
| **安全认证** | API Key 认证、RBAC 角色授权、多租户隔离 |

## 正确的工作流程

**Claude Code + ChipAgent = LLM 设计 + 可信 EDA 工具反馈**

```
用户: "设计一个 RISC-V CPU 子系统，并持续验证 RTL 质量"
  ↓
Claude (LLM):
  ├─ 理解需求和外部规范
  ├─ 设计架构、拆分模块
  ├─ 生成代码 (3000+ 行 Verilog)
  └─ 迭代优化 (根据验证反馈)
  ↓ 调用 MCP 工具
ChipAgent (MCP Server):
  ├─ chipagent_elaborate()      → Verilator lint / Yosys 草估
  ├─ chipagent_run_simulation() → Icarus Verilog / Verilator 仿真
  ├─ chipagent_analyze_waveform() → VCD/FST 事务时间线与故障证据
  ├─ chipagent_run_synthesis()  → Yosys 综合
  ├─ chipagent_analyze_timing() → OpenSTA 时序分析
  ├─ chipagent_run_flow(run_physical=True) → RTL 到 ASAP7 DEF/GDS
  └─ chipagent_run_physical_flow_asap7()   → ASAP7 OpenROAD Flow Scripts
  ↓ 返回真实结果
Claude: 分析结果，继续优化...
```

波形分析不提供 GUI，也不会猜测 RTL 意图。调用方显式描述协议，例如：

```python
from chipagent.mcp import chipagent_analyze_waveform

chipagent_analyze_waveform("sim.vcd", [{
    "name": "l2_request",
    "clock": "tb.clock",
    "valid": "tb.dut.io_request_valid",
    "ready": "tb.dut.io_request_ready",
    "id": "tb.dut.io_request_bits_transactionId",
    "payload": ["tb.dut.io_request_bits_address"],
    "role": "request",
    "pair": "l2",
    "max_stall_time": 20,
    "max_response_time": 200,
    "source": {"file": "SharedL2Cache.scala", "line": 250}
}], output_dir="reports")
```

结果包含 `failure.json` 和 `timeline.md`，可检测 stalled payload 变化、
stall timeout、重复在途 ID、无请求响应、丢失响应和响应超时。FST 输入需要
系统中存在 `fst2vcd`；否则工具会明确返回 unavailable/error，不伪造分析。

**关键**: Claude 生成代码，ChipAgent 验证代码。各司其职。

## 快速开始

### 安装

```bash
pip install -e .
```

依赖：Python ≥ 3.10，`mcp`、`langgraph`、`openai`、`temporalio`。

默认使用 `bash scripts/setup_eda_env.sh --docker` 配置全部工具环境。ChipAgent 会优先使用 host 上的 EDA 工具；host 缺失时，工具会自动选择对应 Docker 镜像执行。
内部采用分层镜像：基础工具使用 `chipagent/tools:latest`，OpenROAD/OpenSTA 使用 `chipagent/openroad:latest`。用户正常调用 MCP 工具时不需要手动选择镜像。
需要真实 EDA 的工具在 host 和 Docker 都不可用时应明确返回不可用/错误；PPA 静态估算类工具会显式标记为估算。

处理不可信 RTL、testbench 或 DPI C++ 时，应设置
`CHIPAGENT_SANDBOX=docker`。Docker 模式默认 fail closed：Docker CLI
不可用时不会静默回退到宿主机。只有处理可信本地输入且明确接受风险时，才可设置
`CHIPAGENT_ALLOW_HOST_FALLBACK=1`；直接使用 `host` 模式不构成安全隔离。

检查和配置工具链：

```bash
python -m chipagent.toolchain --pretty
bash scripts/setup_eda_env.sh --docker
bash scripts/setup_eda_env.sh --smoke
```

如果 Docker Hub 访问受限但本地已有 `ubuntu:latest`，可用：

```bash
CHIPAGENT_BASE_IMAGE=ubuntu:latest bash scripts/setup_eda_env.sh --docker
```

如果默认配置没有找到 OpenROAD/OpenSTA，而你已有包含 `openroad` 和 `sta` 的内部/外部镜像，可包装成 ChipAgent heavy image：

```bash
CHIPAGENT_OPENROAD_BASE_IMAGE=<image-with-openroad-and-sta> bash scripts/setup_eda_env.sh --docker-openroad
bash scripts/setup_eda_env.sh --smoke-openroad
```

### 一键自检示例

环境配好后，最推荐先跑内置 `tiny_counter` 示例。它会从 RTL/testbench 跑到综合、形式等价，并在 `run_physical=True` 时通过 ASAP7/ORFS 生成 DEF/GDS：

```bash
python - <<'PY'
import json
from chipagent.mcp import chipagent_run_example_flow

result = json.loads(chipagent_run_example_flow(run_physical=True))
print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
PY
```

典型关键产物：

```text
generated/examples/tiny_counter/tiny_counter_flow_report.json
generated/examples/tiny_counter/tiny_counter_flow_summary.html
generated/examples/tiny_counter/physical_asap7/orfs-work/results/base/6_final.def
generated/examples/tiny_counter/physical_asap7/orfs-work/results/base/6_final.gds
generated/examples/tiny_counter/physical_asap7/orfs-work/results/base/6_final.odb
```

`summary.trust` 会标出哪些结果来自真实工具，哪些仍是 `static_estimate`。例如没有 Liberty 输入时，顶层 timing/power 仍是结构估算；ASAP7 physical 的 QoR 来自 OpenROAD/ORFS。
HTML summary 会集中展示 flow 状态、QoR、artifact 链接和 ORFS 生成的 placement/routing/congestion 等图片。

Flow 报告保留兼容的二值 `status`，并提供更具体的 `outcome`：
`success`、`partial`、`failed` 或 `unavailable`。其中
`design_failures` 表示 RTL/验证结果失败，`infrastructure_failures` 和
`unavailable_steps` 表示缺少 Yosys、OpenSTA、OpenROAD 等环境能力，便于调用方判断应修改设计还是配置工具链。

ASAP7 physical flow 默认启用缓存：当 RTL、模块名、clock/placement 参数和 OpenROAD 镜像一致，且已有 DEF/GDS/log 时会直接复用结果。需要强制重跑时，可调用 `chipagent_run_physical_flow_asap7(..., clean=True)`，或在 `chipagent_run_flow` 中设置 `physical_clean=True`。

对于布局布线后暴露出的单点高扇出网络，ASAP7 flow 支持定点修复而不是全局
`set_max_fanout` 过缓冲：传入 `high_fanout_nets`（如
`["storeTable.pendingEntry"]`）和 `high_fanout_max` 后，ChipAgent 会在
placement repair 阶段用 `insert_buffer` 只拆分指定层次化 net，并自动按
`cell_vt` 选择 RVT/LVT/SLVT buffer 单元。全局 fanout 约束会造成整个设计
过度插 buffer，这一参数用于替代它。

推荐入口是工艺无关的 `chipagent_run_physical_flow`：它把 `platform` 作为
显式参数（当前支持 `asap7`），工艺相关设置放在 `platform_options` 里；
`chipagent_run_physical_flow_asap7` 保留为 ASAP7 兼容接口，内部仍转发到
同一个 ORFS 后端。未来接入其他工艺库时不需要再改调用方 API。

### 方式一：Claude Code 集成（推荐）

仓库根已有 `.mcp.json`，Claude Code 打开项目后**自动发现** ChipAgent MCP Server。

```json
{
  "mcpServers": {
    "chipagent": {
      "command": "python",
      "args": ["-m", "chipagent.mcp"],
      "env": { "CHIPAGENT_DISABLE_LLM": "1" }
    }
  }
}
```

接入后在 Claude Code 里直接用自然语言对话：

```
你：给 UART 做一套完整设计，从 RTL 到驱动
Claude：（生成代码，调用 chipagent 验证工具，迭代优化）

你：DMA 面积别超 300 cell，跑 250MHz
Claude：（调 chipagent_run_dse，返回 tradeoff 表 + 选定设计）
```

详见 [CLAUDE_CODE_INTEGRATION.md](CLAUDE_CODE_INTEGRATION.md)。

### 方式二：CLI 直接使用

```bash
# 历史入口：只适合生成简单模板/脚手架，不推荐用于复杂 RTL 设计
chipagent "请为 AXI DMA 模块生成 RTL 模板" --output-dir ./generated

# 设计空间探索
python -m chipagent.workflow dse "设计一个 AXI DMA" --targets area=300,fmax=250

# MCP 服务（推荐通过 Claude Code 或其他 MCP host 使用）
python -m chipagent.mcp
```

详见 [examples/README.md](examples/README.md)。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                  Claude Code                        │
│           (推理层：理解需求 + 生成代码 + 决策)         │
└──────────────────────┬──────────────────────────────┘
                       │ MCP (stdio)
┌──────────────────────▼──────────────────────────────┐
│              ChipAgent MCP Server (53 tools)         │
│                  (chipagent.mcp)                     │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │  综合    │ │ 物理设计 │ │  验证   │ │ 知识库 │ │
│  │  分析    │ │  流程    │ │  仿真   │ │  查询  │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ 多Agent  │ │ 服务注册 │ │  安全   │ │ 任务   │ │
│  │  协作    │ │  路由    │ │  RBAC   │ │ 面板   │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│                                                      │
│  ┌─────────────────────────────────────────────────┐ │
│  │   7 Skills + 20 Python Tools + 任务面板 + Temporal │ │
│  └─────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### MCP 工具清单（53 个）

| 类别 | 工具数 | 工具 |
|---|---|---|
| 设计探索 | 2 | `chipagent_run_dse` · `chipagent_run_skill` |
| SV 参数探索 | 1 | `chipagent_run_sv_parameter_dse` |
| 综合 | 5 | `chipagent_run_synthesis` · `chipagent_analyze_timing` · `chipagent_optimize_area` · `chipagent_analyze_power` · `chipagent_run_formality` |
| 物理设计 | 7 | `chipagent_run_physical_flow_asap7` · `chipagent_create_floorplan` · `chipagent_run_placement` · `chipagent_run_cts` · `chipagent_run_routing` · `chipagent_run_drc_check` · `chipagent_run_lvs_check` |
| 端到端流程 | 2 | `chipagent_run_flow` · `chipagent_run_example_flow` |
| 验证与量测 | 6 | `chipagent_run_simulation` · `chipagent_analyze_waveform` · `chipagent_elaborate` · `chipagent_check_register_alignment` · `chipagent_check_sw_hw_interface` · `chipagent_analyze_coverage` |
| PPA 分析 | 4 | `chipagent_estimate_area` · `chipagent_estimate_performance` · `chipagent_estimate_power` · `chipagent_check_ppa_targets` |
| 知识库 | 7 | `chipagent_query_knowledge_base` · `chipagent_search_code_examples` · `chipagent_consult_architecture` · `chipagent_diagnose_issue` · `chipagent_generate_documentation` · `chipagent_search_software_reference` · `chipagent_consult_sw_hw_co_design` |
| 多 Agent 协作 | 2 | `chipagent_coordinate_hw_sw_codesign` · `chipagent_execute_agent_workflow` |
| 服务注册/工具链 | 3 | `chipagent_check_toolchain` · `chipagent_list_available_tools` · `chipagent_discover_services` |
| 安全 | 2 | `chipagent_auth_status` · `chipagent_check_permission` |
| 能力查询 | 2 | `chipagent_list_skills` · `chipagent_list_tools` |
| 任务面板 | 7 | `chipagent_task_submit` · `chipagent_task_approve` · `chipagent_task_rollback` · `chipagent_task_list` · `chipagent_task_status` · `chipagent_task_pause` · `chipagent_task_resume` |
| 协同仿真 | 2 | `chipagent_run_sw_hw_cosim` · `chipagent_run_dpi_cosim` |
| 干跑模式 | 1 | `chipagent_dry_run` |

### 文本型 Skill（7 个）

| Skill | 产出 |
|---|---|
| `rtl_generation` | Verilog/SystemVerilog 模块 |
| `reg_definition` | SV 寄存器块 |
| `register_header` | C 头文件 `<block>_regs.h` |
| `hal_library` | C HAL 层骨架 |
| `testbench_generation` | SV testbench（自检测试） |
| `uvm_skeleton` | UVM 平台（env/agent/seq/driver） |
| `linux_driver` | Linux platform/char device 驱动 |

## 历史 Phase 3 功能

Phase 3 曾把 ChipAgent 描述为“完整芯片设计自动化平台”。该表述已废弃。
当前口径是：ChipAgent 提供 MCP 工具服务层，Claude/LLM 负责设计与代码生成。

### 主要新增模块

- **综合 MCP Server（5 工具）**：Yosys 综合、时序分析、面积优化、功耗分析、形式验证
- **物理设计 MCP Server（7 工具）**：ASAP7 physical flow、Floorplan、Placement、CTS、Routing、DRC、LVS
- **端到端 EDA Flow（2 工具）**：通用 flow、内置 tiny_counter example flow
- **知识库 MCP Server（7 工具）**：TF-IDF 检索、代码示例搜索、架构咨询、问题诊断
- **多 Agent 协作（2 工具）**：HW/SW 协同设计、自定义 Agent 工作流
- **服务注册与工具链（3 工具）**：工具发现、服务路由、工具链检查
- **安全模块（2 工具）**：认证状态、权限检查
- **DPI 协同仿真（1 工具）**：Verilator DPI 模式

### 新增子包

```
chipagent/
├── knowledge/          # 知识库（TF-IDF 检索）
├── agents/             # 多 Agent 协作（Hardware/Verification/Software）
├── registry/           # 服务注册与发现
├── router/             # 任务路由
├── security/           # 安全模块（认证/授权/审计）
└── tools/              # 新增 12 个工具文件
    ├── synth_*.py      # 5 个综合工具
    ├── phys_*.py       # 6 个物理设计工具
    └── dpi_cosim.py    # DPI 协同仿真
```

## 测试

```bash
pytest                              # 全套测试
pytest tests/test_phase3.py -v      # Phase 3 测试（27 个）
pytest tests/test_chat_mcp.py       # MCP 工具 + 意图路由
```

183 passed / 3 skipped，零回归。

## 项目文档

| 文档 | 说明 |
|---|---|
| [CLAUDE_CODE_INTEGRATION.md](CLAUDE_CODE_INTEGRATION.md) | Claude Code 集成指南（完整工具清单 + 使用示例） |
| [examples/README.md](examples/README.md) | 详细使用示例（MCP + CLI） |
| [ChipAgent_Architecture_v3.0.md](ChipAgent_Architecture_v3.0.md) | 当前架构来源：EDA 工具 MCP 服务层 |
| [REFACTORING_PLAN.md](REFACTORING_PLAN.md) | 从全能 AI 转向 EDA 工具服务层的重构计划 |

## 许可

内部项目。
