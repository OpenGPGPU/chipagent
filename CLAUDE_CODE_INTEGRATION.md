# ChipAgent — Claude Code 集成指南

ChipAgent 是一个**芯片设计领域的 EDA 工具 MCP 服务层**，让 Claude Code 可以通过 MCP 协议调用真实的芯片设计工具（Yosys、OpenROAD、Verilator 等）。

当前架构以 [ChipAgent_Architecture_v3.0.md](ChipAgent_Architecture_v3.0.md) 为准。旧版
“AI 芯片开发智能体系统 / 全流程自动化平台”表述已经废弃。

**重要**: ChipAgent 不是"AI 芯片设计师"，而是"EDA 工具的服务层"。
- ✅ **ChipAgent 负责**：封装真实 EDA 工具、提供验证能力
- ✅ **Claude Code 负责**：理解需求、生成代码、迭代优化
- ❌ **ChipAgent 不负责**：复杂/生产级代码生成、架构设计决策

接入后 Claude Code 会话里可以直接用自然语言驱动芯片设计流程，不需要改 Claude Code 一行代码。

---

## 1. 接入方式（任选其一）

### A. 项目级 `.mcp.json`（推荐，团队共享）

仓库根目录已有 `.mcp.json`，克隆仓库后 Claude Code 自动发现：

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

### B. 命令行一次性注册

```bash
claude mcp add chipagent -- python -m chipagent.mcp
```

### C. 用户级全局注册

```bash
claude mcp add -s user chipagent -- python -m chipagent.mcp
```

> **为什么设 `CHIPAGENT_DISABLE_LLM=1`？**
> Claude Code 已经是推理层，chipagent 内部再调 LLM 会重复计费且增加延迟。
> 关闭后 chipagent 专注于后端量测、验证、知识检索和模板脚手架。

---

## 2. MCP 工具清单（共 52 个）

### 设计探索（2 个）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_run_dse` | HW/SW 协同 DSE：给需求 + 非功能目标，探索微架构变体（pipeline/width/gating/interface），量测 area/Fmax/latency/power/sw-cost，保留 Pareto 前沿，返回选定设计 + tradeoff 表 + 分区理由 | "DMA 面积 < 300 cell，跑 250MHz" |
| `chipagent_run_skill` | 运行任意文本型 Skill（rtl/reg/header/hal/driver/tb/uvm），定位为模板脚手架/辅助产物，不是复杂芯片设计生成器 | "用 testbench_generation 给 DMA 写 TB" |

### SV 参数探索（1 个）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_run_sv_parameter_dse` | 对已有参数化 RTL 做参数网格探索，复用仿真/综合/可选 ASAP7 physical，并按 QoR 排序 | "扫一下 WIDTH/DEPTH 参数组合" |

### 综合（5 个 - Phase 3 新增）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_run_synthesis` | Yosys 综合：RTL → 网表，返回 cell count 和 area | "综合这个设计" |
| `chipagent_analyze_timing` | 静态时序分析：返回 slack、WNS、TNS、关键路径 | "时序分析怎么样" |
| `chipagent_optimize_area` | 面积优化：应用 opt_clean、opt_merge 等策略 | "优化一下面积" |
| `chipagent_analyze_power` | 功耗分析：估算动态/漏电/总功耗 | "功耗多少" |
| `chipagent_run_formality` | 形式等价性检查：比较参考与实现网表 | "验证等价性" |

### 物理设计（7 个 - Phase 3 新增）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_run_physical_flow_asap7` | 通过 ASAP7 OpenROAD Flow Scripts 从 RTL 跑到 DEF/GDS，并解析 QoR | "用 ASAP7 跑一次物理实现" |
| `chipagent_create_floorplan` | 创建 floorplan：die area、core area、utilization | "创建 floorplan" |
| `chipagent_run_placement` | 单元放置：全局 + 详细放置 | "运行放置" |
| `chipagent_run_cts` | 时钟树综合：skew、insertion delay、buffer count | "时钟树综合" |
| `chipagent_run_routing` | 信号布线：全局 + 详细布线 | "运行布线" |
| `chipagent_run_drc_check` | 设计规则检查：violations count 和 categories | "DRC 检查" |
| `chipagent_run_lvs_check` | 版图与原理图检查：match status 和 mismatches | "LVS 检查" |

### 端到端流程（2 个 - Phase 3 新增）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_run_flow` | 对已有 RTL/testbench/layout/netlist 运行仿真、综合、PPA 结构估算、Formality、DRC/LVS，并汇总 artifact 报告 | "对这个 adder 跑完整 EDA 检查" |
| `chipagent_run_example_flow` | 运行内置 tiny_counter 自检流程，验证工具链和报告生成 | "跑内置示例检查环境" |

### 验证与量测（5 个）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_run_simulation` | 编译跑 RTL + testbench（iverilog/verilator），报 pass/fail + VCD 路径 | "跑一下这个 TB 看过不过" |
| `chipagent_elaborate` | verilator lint + yosys 综合草估，返回 cell count / lint issues | "这份 RTL 面积大概多少" |
| `chipagent_check_register_alignment` | RTL 寄存器块偏移 vs C 头文件 define 一致性检查 | "寄存器和头文件对得上吗" |
| `chipagent_check_sw_hw_interface` | 驱动接口契约 vs RTL（include/compatible string/MMIO helpers/port references） | "驱动和 RTL 接口匹配吗" |
| `chipagent_analyze_coverage` | 仿真覆盖率分析：有覆盖率报告则解析；没有真实覆盖率数据时必须明确标记为 stub/derived | "覆盖率多少" |

### PPA 分析（4 个 - Phase 3 新增）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_estimate_area` | 面积估算：基于 RTL 结构分析估算门数、寄存器、组合逻辑，返回复杂度评分（0-100） | "估算一下面积" |
| `chipagent_estimate_performance` | 性能估算：分析关键路径深度，估算最大频率，评估时序风险（low/medium/high） | "性能怎么样" |
| `chipagent_estimate_power` | 功耗估算：分析活动因子，估算动态/静态功耗，返回功耗分解 | "功耗多少" |
| `chipagent_check_ppa_targets` | PPA 目标检查：综合分析面积/性能/功耗，对比目标值，提供优化建议 | "检查是否满足 PPA 目标" |

### 知识库（7 个 - Phase 3 新增）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_query_knowledge_base` | 查询设计知识：TF-IDF 检索相关文档 | "查询 AXI 协议规范" |
| `chipagent_search_code_examples` | 搜索代码示例：从 skills/examples 目录 | "找 FIFO 实现示例" |
| `chipagent_consult_architecture` | 架构咨询：搜索架构模式和最佳实践 | "流水线处理器设计建议" |
| `chipagent_diagnose_issue` | 问题诊断：搜索类似问题及解决方案 | "关键路径时序违例" |
| `chipagent_generate_documentation` | 文档生成：基于知识库生成模块文档 | "给 adder 生成文档" |
| `chipagent_search_software_reference` | 软件参考搜索：API 文档、驱动指南 | "寄存器读取函数" |
| `chipagent_consult_sw_hw_co_design` | SW/HW 协同设计咨询：接口规范、集成指南 | "自定义外设驱动设计" |

### 多 Agent 协作（2 个 - Phase 3 新增）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_coordinate_hw_sw_codesign` | HW/SW 协同设计协调：编排硬件、软件、验证 Agent | "UART 外设完整设计" |
| `chipagent_execute_agent_workflow` | 自定义多 Agent 工作流执行 | "执行自定义 Agent 流程" |

### 服务注册/工具链（3 个）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_check_toolchain` | 检查本地 EDA 工具链并给出 Docker/apt/manual 配置建议 | "检查工具环境" |
| `chipagent_list_available_tools` | 按类别列出可用工具 | "列出综合类工具" |
| `chipagent_discover_services` | 按能力发现服务 | "发现综合相关服务" |

### 安全（2 个 - Phase 3 新增）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_auth_status` | 检查认证状态 | "当前认证状态" |
| `chipagent_check_permission` | 检查工具访问权限 | "我可以用综合工具吗" |

### 能力查询（2 个）

| 工具 | 作用 |
|---|---|
| `chipagent_list_skills` | 列出所有文本型 Skill（7 个） |
| `chipagent_list_tools` | 列出所有 Python 后端工具（20 个） |

### 任务面板（7 个）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_task_submit` | 提交任务（可设 require_approval 审批门） | "提交这个设计任务，需要审批" |
| `chipagent_task_approve` | 审批通过，产物落地 | "批准 task-20250713-001" |
| `chipagent_task_rollback` | 回滚已落地的产物 | "回滚上次提交" |
| `chipagent_task_list` | 列出所有已提交的任务 | "看看有哪些任务" |
| `chipagent_task_status` | 查看单个任务的详细状态 | "task-xxx 到哪了" |
| `chipagent_task_pause` | 暂停运行中的任务 | "暂停这个任务" |
| `chipagent_task_resume` | 恢复已暂停的任务 | "继续刚才的任务" |

### 协同仿真（2 个）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_run_sw_hw_cosim` | HW/SW 协同仿真接口分析；当前主要是结构/stub 路径 | "检查驱动和 RTL 交互意图" |
| `chipagent_run_dpi_cosim` | DPI-based C/C++ 与 Verilog 协同仿真，需 Verilator 后端 | "跑 DPI 协同仿真" |

### 干跑模式（1 个）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `chipagent_dry_run` | 预览计划、命令和产物，不执行高风险工具 | "先看看会跑哪些步骤" |

---

## 3. MCP Resources（共 3 个）

Claude Code 可以通过 MCP resource 协议读取 chipagent 的状态：

| Resource URI | 说明 |
|---|---|
| `file://artifacts/` | 已生成产物清单（`generated/` 目录的文件列表和大小） |
| `file://register-definitions/` | 寄存器定义 Skill 库的参考文档和示例 |
| `file://audit-log/` | 执行审计日志（`logs/` 目录下的 JSON/JSONL 文件） |

---

## 4. 文本型 Skill 库（共 7 个）

每个 Skill 是纯文本目录（`SKILL.md` + examples + reference），零 Python 代码即可
扩展。通过 `chipagent_run_skill` 或闭环流程调用：

| Skill | task_type | 产出 | 后校验 |
|---|---|---|---|
| RTL 生成 | `rtl_generation` | Verilog/SystemVerilog 模块 | elaborate |
| 寄存器定义 | `reg_definition` | SV 寄存器块（CPU 读写译码 + 地址映射） | elaborate |
| 寄存器头文件 | `register_header` | C 头文件 `<block>_regs.h` | alignment vs RTL |
| HAL 库 | `hal_library` | C HAL 层骨架 | — |
| Testbench | `testbench_generation` | SV testbench（自检测试） | simulation vs RTL |
| UVM 骨架 | `uvm_skeleton` | UVM 平台（env/agent/seq/driver） | — |
| Linux 驱动 | `linux_driver` | platform/char device 驱动 | interface vs RTL |

---

## 5. 在 Claude Code 里怎么用

接入后直接用自然语言对话，Claude 自动选择工具：

### 设计探索

```
你：我给你几个 DMA RTL 变体，帮我比较面积和频率
Claude：（调 chipagent_run_dse / PPA / synthesis 工具，返回 tradeoff 表）
      → 跟你讨论选哪个变体，解释为什么选 v6

你：把上次的方案再改小一点
Claude：（收紧 area_budget 重跑 DSE）
```

### 完整设计流程

```
你：给 UART 做一套完整设计，从 RTL 到驱动
Claude：（自己生成 RTL + 头文件 + HAL + TB + 驱动代码）
      → 调用 chipagent_elaborate 检查语法/可综合性
      → 调用 chipagent_run_simulation 运行仿真
      → 调用 chipagent_check_register_alignment 检查对齐
      → 根据错误迭代优化
      → 返回全套产物 + 验证报告

你：寄存器对齐检查通过了吗？
Claude：（读 alignment 结果，指出不一致的地方）
```

### 单步操作

```
你：跑一下这个 testbench 看仿真过不过
Claude：（调 chipagent_run_simulation）

你：这份 RTL 综合面积大概多少？
Claude：（调 chipagent_elaborate）

你：驱动和 RTL 的接口匹配吗？
Claude：（调 chipagent_check_sw_hw_interface）
```

### 任务审批

```
你：提交一个 UART 设计任务，需要我审批才能落地
Claude：（调 chipagent_task_submit with require_approval=true）
      → 产物暂存，等你审批

你：批准 task-20250713-001
Claude：（调 chipagent_task_approve，产物写入 generated/）
```

### Phase 3 综合流程（新增）

```
你：综合这个设计并分析时序
Claude：（调 chipagent_run_synthesis）
      → 返回综合报告：cell count、area、netlist
      → （调 chipagent_analyze_timing）
      → 返回时序报告：slack、WNS、TNS、critical path
```

### Phase 3 物理设计流程（新增）

```
你：运行完整的物理设计流程
Claude：（依次调用）
      → chipagent_create_floorplan（创建 floorplan）
      → chipagent_run_placement（单元放置）
      → chipagent_run_cts（时钟树综合）
      → chipagent_run_routing（信号布线）
      → chipagent_run_drc_check（设计规则检查）
      → chipagent_run_lvs_check（版图与原理图检查）
      → 返回完整物理设计报告
```

### Phase 3 多 Agent 协作（新增）

```
你：设计一个 UART 外设，包含硬件和驱动
Claude：（优先自行规划并生成代码，再调用 ChipAgent 工具验证）
      → 多 Agent 工具属于历史/实验能力
      → 不应把它当作生产级自主设计流程
```

### Phase 3 知识库查询（新增）

```
你：查询 AXI 协议的设计规范
Claude：（调 chipagent_query_knowledge_base）
      → 搜索知识库，返回相关文档片段和示例
      → 提供设计建议和最佳实践
```

---

## 6. 交互式 CLI（不用 Claude Code 时）

不想用 Claude Code、直接在终端对话：

```bash
python -m chipagent.workflow chat           # LLM 网关模式（流式回复）
python -m chipagent.workflow chat --no-llm  # 离线确定性模式
```

特点：
- **多轮上下文**：记得上一轮的 spec / 选定设计 / tradeoff 表
- **LLM 主导意图 + 规则兜底**：明确设计请求路由到 DSE
- **流式打字回复**：走 SSE 逐字输出
- **可追问/微调**："为什么选 v6""再小一点""看 v3 的 RTL"

---

## 7. 其它入口（仍可用）

```bash
# 直接跑 DSE
python -m chipagent.workflow dse "..." --targets area=300,fmax=250

# Task 面板
python -m chipagent.workflow task submit "..." --require-approval

# 历史模板入口：只适合简单脚手架，不建议用于复杂 RTL 设计
python -m chipagent "请为 AXI DMA 生成 RTL 模板" --output-dir ./generated

# 冒烟测试 MCP server
python -m chipagent.mcp --list     # 打印工具清单
python -m chipagent.mcp            # 起 stdio server
```

---

## 8. 安装

```bash
pip install -e .
```

依赖：Python ≥ 3.10，`mcp`、`langgraph`、`openai`、`temporalio`（见 `pyproject.toml`）。

默认使用 `bash scripts/setup_eda_env.sh --docker` 配置全部工具环境。ChipAgent 会优先使用 host 上的 EDA 工具；host 缺失时，工具会自动选择对应 Docker 镜像执行。
内部采用分层镜像：基础工具使用 `chipagent/tools:latest`，OpenROAD/OpenSTA 使用 `chipagent/openroad:latest`。用户正常调用 MCP 工具时不需要手动选择镜像。
需要真实 EDA 的工具在 host 和 Docker 都不可用时应明确返回不可用/错误；静态 PPA 工具会显式标记为估算。

推荐先检查工具环境：

```bash
python -m chipagent.toolchain --pretty
bash scripts/setup_eda_env.sh --docker
bash scripts/setup_eda_env.sh --smoke
```

若默认 base image 因网络或证书问题无法拉取，但本地已有 `ubuntu:latest`：

```bash
CHIPAGENT_BASE_IMAGE=ubuntu:latest bash scripts/setup_eda_env.sh --docker
```

若默认配置没有找到 OpenROAD/OpenSTA，而你已有包含 `openroad` 和 `sta` 的镜像，可包装成 ChipAgent heavy image：

```bash
CHIPAGENT_OPENROAD_BASE_IMAGE=<image-with-openroad-and-sta> bash scripts/setup_eda_env.sh --docker-openroad
bash scripts/setup_eda_env.sh --smoke-openroad
```

---

## 9. 测试

```bash
pytest                              # 全套测试
pytest tests/test_chat_mcp.py       # MCP 工具 + 意图路由（18 条）
```

全套 183 passed / 3 skipped，零回归。离线模式下在线用例自动 skip。

---

## 10. 架构速览

```
┌─────────────────────────────────────────────────────┐
│                  Claude Code                        │
│           (推理层：理解需求 + 决策)                    │
└──────────────────────┬──────────────────────────────┘
                       │ MCP (stdio)
┌──────────────────────▼──────────────────────────────┐
│              ChipAgent MCP Server (52 tools)         │
│                  (chipagent.mcp)                     │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │  模板    │ │  综合    │ │ 物理设计 │ │  验证  │ │
│  │  脚手架  │ │  分析    │ │  流程    │ │  仿真  │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ 知识库   │ │ 多Agent  │ │ 服务注册 │ │  安全  │ │
│  │  查询    │ │  协作    │ │  路由    │ │  RBAC  │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│                                                      │
│  ┌─────────────────────────────────────────────────┐ │
│  │   7 Skills + 20 Python Tools + 任务面板 + Temporal │ │
│  └─────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

## 11. 历史 Phase 3 功能总结

Phase 3 曾把 ChipAgent 描述为完整芯片设计自动化平台。该表述已废弃。
当前口径是：Claude/LLM 负责设计与代码生成，ChipAgent 提供可信 MCP 工具服务。

### 主要新增模块

| 模块 | 工具数 | 说明 |
|---|---|---|
| Synthesis MCP Server | 5 | Yosys 综合、时序分析、面积优化、功耗分析、形式验证 |
| Physical Design MCP Server | 7 | ASAP7 physical flow、Floorplan、Placement、CTS、Routing、DRC、LVS |
| End-to-end EDA Flow | 2 | 通用 flow、内置 tiny_counter example flow |
| Knowledge MCP Server | 7 | 知识库查询、代码示例、架构咨询、问题诊断、文档生成 |
| Multi-Agent Collaboration | 2 | HW/SW 协同设计协调、自定义 Agent 工作流 |
| Service Registry / Toolchain | 3 | 工具列表、服务发现、工具链检查 |
| Security | 2 | 认证状态、权限检查 |

### 新增子包

```
chipagent/
├── knowledge/          # 知识库（TF-IDF 检索）
│   ├── embedder.py     # 文本嵌入
│   ├── indexer.py      # 文档索引
│   ├── retriever.py    # 混合检索
│   └── base.py         # 知识库基类
├── agents/             # 多 Agent 协作
│   ├── base_agent.py   # Agent 基类
│   ├── hardware_agent.py  # 硬件 Agent
│   ├── verification_agent.py  # 验证 Agent
│   ├── software_agent.py  # 软件 Agent
│   └── coordinator.py  # Agent 协调器
├── registry/           # 服务注册
│   ├── registry.py     # 工具注册表
│   └── discovery.py    # 服务发现
├── router/             # 任务路由
│   └── router.py       # 任务路由器
└── security/           # 安全模块
    ├── auth.py         # 认证
    ├── authorization.py  # 授权（RBAC）
    └── audit.py        # 审计日志
```

### 新增工具文件

```
chipagent/tools/
├── synth_run.py        # Yosys 综合
├── synth_timing.py     # 时序分析
├── synth_area.py       # 面积优化
├── synth_power.py      # 功耗分析
├── synth_formality.py  # 形式验证
├── phys_floorplan.py   # Floorplan
├── phys_placement.py   # Placement
├── phys_cts.py         # CTS
├── phys_routing.py     # Routing
├── phys_drc.py         # DRC
├── phys_lvs.py         # LVS
└── dpi_cosim.py        # DPI 协同仿真
```

### 测试覆盖

- Phase 3 测试：27 个测试用例
- 总测试：183 passed, 3 skipped
- 覆盖率：所有新增模块均有测试

### 工具总数演进

| Phase | 工具数 | 新增 |
|---|---|---|
| Phase 1 | 20 | 基础能力 |
| Phase 2 | 20 | 优化完善 |
| Phase 3+ | **52** | +32 工具 |

这些模块仍可作为 MCP 工具或历史实验能力使用，但不能作为“自主芯片设计平台”的产品承诺。
