# ChipAgent 重构计划：从"全能 AI"到"EDA 工具服务层"

> Active direction, historical details.
>
> 本文档的方向仍然有效：从“全能 AI”收敛为“EDA 工具服务层”。
> 但其中部分工具名和工具数量来自重构提议阶段，例如
> `chipagent_lint` / `chipagent_simulate`。当前实际 MCP 名称以
> `python -m chipagent.mcp --list` 和 [README.md](README.md) 为准。
> 当前架构以 [ChipAgent_Architecture_v3.0.md](ChipAgent_Architecture_v3.0.md) 为准。

## 执行摘要

**核心问题**: ChipAgent 试图成为"AI 芯片设计师"，但实际上只是一个简单的模板生成器 + 假数据返回器。这导致用户无法信任系统，无法完成真实设计任务。

**解决方案**: 重新定位为"EDA 工具的 MCP 服务层"，让 LLM (Claude/GPT-4) 负责设计和决策，ChipAgent 提供可信的验证工具。

**目标**: 从 45 个工具（大部分返回假数据）重构为 ~20 个高质量工具（全部调用真实 EDA 工具）。

---

## 1. 问题诊断

### 1.1 当前架构的根本缺陷

```
❌ 当前错误架构:
User → ChipAgent (尝试理解需求 + 生成代码 + 返回假数据)
         ↑
         解析器太弱（关键词匹配）
         模板太简单（8-bit 寄存器）
         假数据不可信
```

**问题根源**:
1. **定位错误**: ChipAgent 不是 AI，无法理解复杂需求
2. **重复造轮子**: LLM 已经能生成代码，ChipAgent 不需要
3. **假数据**: 没有真实 EDA 工具就返回"估算值"，毫无意义

### 1.2 Demo 验证结果

**测试场景**: 用户要求"设计一个 RISC-V CPU"

| 方式 | 代码量 | 复杂度 | 结果 |
|------|--------|--------|------|
| Claude 生成 + ChipAgent 验证 | 9330 字符 | 完整 RV32I 核心 | ✅ 可行 |
| chipagent_generate_rtl | 1150 字符 | 8-bit 寄存器 | ❌ 完全不匹配 |

**结论**: LLM + ChipAgent 工具的正确工作流是可行的。

---

## 2. 工具分类与处置方案

### 2.1 工具分类表

| 工具名 | 当前状态 | 问题 | 处置方案 |
|--------|----------|------|----------|
| **代码生成类** | | | |
| `chipagent_generate_rtl` | ❌ 删除 | 模板太简单，无法生成真实设计 | **删除** |
| `chipagent_run_skill` | ⚠️ 保留但重命名 | 名字不准确，改为 `chipagent_apply_skill_template` | **重命名** |
| `chipagent_run_closed_loop` | ❌ 删除 | 依赖 generate_rtl，同样问题 | **删除** |
| `chipagent_run_dse` | ⚠️ 保留但重构 | 设计空间探索有用，但不应该生成代码 | **重构** |
| **验证工具类** | | | |
| `chipagent_lint` | ✅ 保留增强 | 调用 verilator，真实工具 | **保留增强** |
| `chipagent_simulate` | ✅ 保留增强 | 调用 iverilog/verilator，真实工具 | **保留增强** |
| `chipagent_check_register_alignment` | ✅ 保留 | 结构对比，不依赖 EDA | **保留** |
| `chipagent_check_sw_hw_interface` | ✅ 保留 | 结构对比，不依赖 EDA | **保留** |
| **综合工具类** | | | |
| `chipagent_run_synthesis` | ⚠️ 重构 | 没有 yosys 返回假数据 | **重构：没工具就报错** |
| `chipagent_analyze_timing` | ⚠️ 重构 | 没有 OpenSTA 返回假数据 | **重构：没工具就报错** |
| `chipagent_optimize_area` | ⚠️ 重构 | 没有 yosys 返回假数据 | **重构：没工具就报错** |
| `chipagent_analyze_power` | ⚠️ 重构 | 没有工具返回假数据 | **重构：没工具就报错** |
| `chipagent_run_formality` | ⚠️ 重构 | 没有工具返回假数据 | **重构：没工具就报错** |
| **物理设计工具类** | | | |
| `chipagent_create_floorplan` | ⚠️ 重构 | 没有 OpenROAD 返回假数据 | **重构：没工具就报错** |
| `chipagent_run_placement` | ⚠️ 重构 | 没有 OpenROAD 返回假数据 | **重构：没工具就报错** |
| `chipagent_run_cts` | ⚠️ 重构 | 没有 OpenROAD 返回假数据 | **重构：没工具就报错** |
| `chipagent_run_routing` | ⚠️ 重构 | 没有 OpenROAD 返回假数据 | **重构：没工具就报错** |
| `chipagent_run_drc_check` | ⚠️ 重构 | 没有 Magic 返回假数据 | **重构：没工具就报错** |
| `chipagent_run_lvs_check` | ⚠️ 重构 | 没有 Netgen 返回假数据 | **重构：没工具就报错** |
| **知识库工具类** | | | |
| `chipagent_query_knowledge_base` | ✅ 保留 | TF-IDF 检索，不依赖 EDA | **保留** |
| `chipagent_search_code_examples` | ✅ 保留 | 文件搜索，不依赖 EDA | **保留** |
| `chipagent_consult_architecture` | ✅ 保留 | 基于知识库 | **保留** |
| `chipagent_diagnose_issue` | ✅ 保留 | 基于知识库 | **保留** |
| `chipagent_generate_documentation` | ✅ 保留 | 模板生成，不依赖 EDA | **保留** |
| `chipagent_search_software_reference` | ✅ 保留 | 文件搜索 | **保留** |
| `chipagent_consult_sw_hw_co_design` | ✅ 保留 | 基于知识库 | **保留** |
| **任务管理工具类** | | | |
| `chipagent_task_submit` | ✅ 保留 | 项目管理 | **保留** |
| `chipagent_task_approve` | ✅ 保留 | 项目管理 | **保留** |
| `chipagent_task_rollback` | ✅ 保留 | 项目管理 | **保留** |
| `chipagent_task_list` | ✅ 保留 | 项目管理 | **保留** |
| `chipagent_task_status` | ✅ 保留 | 项目管理 | **保留** |
| `chipagent_task_pause` | ✅ 保留 | 项目管理 | **保留** |
| `chipagent_task_resume` | ✅ 保留 | 项目管理 | **保留** |
| **多 Agent 工具类** | | | |
| `chipagent_coordinate_hw_sw_codesign` | ⚠️ 重构 | 依赖 generate_rtl | **重构** |
| `chipagent_execute_agent_workflow` | ⚠️ 重构 | 依赖 generate_rtl | **重构** |
| **服务注册工具类** | | | |
| `chipagent_list_available_tools` | ✅ 保留 | 工具发现 | **保留** |
| `chipagent_discover_services` | ✅ 保留 | 服务发现 | **保留** |
| `chipagent_list_skills` | ✅ 保留 | Skill 列表 | **保留** |
| `chipagent_list_tools` | ✅ 保留 | 工具列表 | **保留** |
| **安全工具类** | | | |
| `chipagent_auth_status` | ✅ 保留 | 认证 | **保留** |
| `chipagent_check_permission` | ✅ 保留 | 授权 | **保留** |
| **其他工具** | | | |
| `chipagent_dry_run` | ⚠️ 重构 | 依赖 generate_rtl | **重构** |
| `chipagent_elaborate` | ✅ 保留增强 | 调用 verilator | **保留增强** |
| `chipagent_run_dpi_cosim` | ✅ 保留 | 调用 verilator DPI | **保留** |

### 2.2 处置统计

| 处置方案 | 数量 | 百分比 |
|----------|------|--------|
| **删除** | 2 | 4% |
| **重构** | 13 | 29% |
| **保留增强** | 2 | 4% |
| **保留** | 27 | 60% |
| **重命名** | 1 | 2% |

**重构后工具数**: ~32 个（删除 2 个，合并部分功能）

---

## 3. 详细重构方案

### 3.1 删除的工具

#### 3.1.1 `chipagent_generate_rtl`

**删除原因**:
- 模板生成器只能产生简单模块（如 8-bit 寄存器）
- 无法理解复杂需求（如 "RISC-V CPU"）
- 与 LLM 能力重复

**替代方案**:
```python
# 用户应该：
# 1. 让 Claude/GPT-4 生成代码
riscv_code = claude.generate("设计 RISC-V CPU")

# 2. 调用 ChipAgent 验证
lint_result = chipagent_lint(code=riscv_code)
sim_result = chipagent_simulate(code=riscv_code, tb=testbench)
```

**实现**:
```python
# 在 chipagent/mcp.py 中删除这个函数
@mcp.tool()
def chipagent_generate_rtl(...) -> str:
    # 删除整个函数
```

#### 3.1.2 `chipagent_run_closed_loop`

**删除原因**:
- 依赖 `generate_rtl`
- 同样的问题

**替代方案**:
```python
# 用户自己实现闭环逻辑
code = claude.generate(requirement)
while not verified:
    lint = chipagent_lint(code)
    if lint['errors']:
        code = claude.fix(lint['errors'])
        continue
    
    sim = chipagent_simulate(code, tb)
    if sim['status'] == 'FAIL':
        code = claude.fix(sim['errors'])
        continue
    
    verified = True
```

### 3.2 重构的工具

#### 3.2.1 综合工具重构

**当前问题**:
```python
def run(self, ctx: ToolContext) -> ToolResult:
    if shutil.which("yosys"):
        return self._run_yosys(...)
    
    # ❌ 问题：返回假数据
    return self._estimate_synthesis(...)  # 正则估算，毫无意义
```

**重构方案**:
```python
def run(self, ctx: ToolContext) -> ToolResult:
    if not shutil.which("yosys"):
        # ✅ 没工具就报错，不要返回假数据
        return ToolResult(
            result={
                "status": "error",
                "message": "Yosys not installed. Please install: https://yosyshq.net/yosys/",
                "required_tool": "yosys"
            },
            issues=["Yosys not available"],
        )
    
    return self._run_yosys(...)
```

**影响的工具**:
1. `chipagent_run_synthesis` → `synth_run.py`
2. `chipagent_analyze_timing` → `synth_timing.py`
3. `chipagent_optimize_area` → `synth_area.py`
4. `chipagent_analyze_power` → `synth_power.py`
5. `chipagent_run_formality` → `synth_formality.py`

**实现步骤**:
```bash
# 1. 修改每个工具文件
for file in chipagent/tools/synth_*.py; do
    # 删除 _estimate_* 方法
    # 修改 run() 方法，没工具就返回 error
done

# 2. 更新测试
pytest tests/test_phase3.py::TestSynthesisTools -v

# 3. 验证
python -c "from chipagent.tools.synth_run import SynthesisTool; print(SynthesisTool().run({'reg_code': 'module top; endmodule'}))"
```

#### 3.2.2 物理设计工具重构

**同样模式**:
```python
def run(self, ctx: ToolContext) -> ToolResult:
    if not shutil.which("openroad"):
        return ToolResult(
            result={
                "status": "error",
                "message": "OpenROAD not installed. Please install: https://openroad.readthedocs.io/",
                "required_tool": "openroad"
            },
            issues=["OpenROAD not available"],
        )
    
    return self._run_openroad(...)
```

**影响的工具**:
1. `chipagent_create_floorplan` → `phys_floorplan.py`
2. `chipagent_run_placement` → `phys_placement.py`
3. `chipagent_run_cts` → `phys_cts.py`
4. `chipagent_run_routing` → `phys_routing.py`
5. `chipagent_run_drc_check` → `phys_drc.py` (需要 Magic)
6. `chipagent_run_lvs_check` → `phys_lvs.py` (需要 Netgen)

### 3.3 增强的工具

#### 3.3.1 `chipagent_lint` 增强

**当前**:
```python
chipagent_lint(code=verilog_code)
```

**增强后**:
```python
chipagent_lint(
    code=verilog_code,
    style_guide="company_style.vlt",      # 自定义风格指南
    severity_level="error",               # error/warning/info
    include_dirs=["./include"],           # include 目录
    define_macros=["SYNTHESIS", "ASIC"],  # 宏定义
    ignore_rules=["UNUSED_SIGNAL"]        # 忽略的规则
)
```

**实现**:
```python
@mcp.tool()
def chipagent_lint(
    code: str,
    style_guide: Optional[str] = None,
    severity_level: str = "warning",
    include_dirs: Optional[List[str]] = None,
    define_macros: Optional[List[str]] = None,
    ignore_rules: Optional[List[str]] = None
) -> str:
    """Lint Verilog code using Verilator."""
    # 构建 verilator 命令
    cmd = ["verilator", "--lint-only"]
    
    if style_guide:
        cmd.extend(["--lint-file", style_guide])
    
    if include_dirs:
        for d in include_dirs:
            cmd.extend(["-I" + d])
    
    if define_macros:
        for m in define_macros:
            cmd.extend(["-D" + m])
    
    # ... 执行并返回结果
```

#### 3.3.2 `chipagent_simulate` 增强

**当前**:
```python
chipagent_simulate(reg_code=rtl, tb_code=testbench)
```

**增强后**:
```python
chipagent_simulate(
    reg_code=rtl,
    tb_code=testbench,
    test_vectors=vectors,           # 测试向量
    timeout=60,                     # 超时（秒）
    coverage=True,                  # 启用覆盖率
    vcd_output="waveform.vcd",      # VCD 输出文件
    defines=["SIMULATION"],         # 宏定义
    plusargs=["+debug", "+trace"],  # +args
    seed=42                         # 随机种子
)
```

### 3.4 重命名的工具

#### 3.4.1 `chipagent_run_skill` → `chipagent_apply_skill_template`

**原因**: 名字不准确，实际上是应用模板，不是"运行 skill"

**实现**:
```python
# 删除旧函数
@mcp.tool()
def chipagent_run_skill(...) -> str:
    # 删除

# 添加新函数
@mcp.tool()
def chipagent_apply_skill_template(
    skill_name: str,
    params: Dict[str, Any]
) -> str:
    """Apply a skill template with given parameters.
    
    Note: This generates simple modules from templates.
    For complex designs, use an LLM (Claude/GPT-4) instead.
    """
    # ... 实现
```

### 3.5 新增的工具

#### 3.5.1 `chipagent_check_tool_availability`

**用途**: 检查 EDA 工具是否安装

```python
@mcp.tool()
def chipagent_check_tool_availability() -> str:
    """Check which EDA tools are available on this system.
    
    Returns a report of installed/missing tools with installation instructions.
    """
    tools = {
        "verilator": "https://verilator.org/guide/latest/install.html",
        "iverilog": "https://steveicarus.github.io/iverilog/usage/installation.html",
        "yosys": "https://yosyshq.net/yosys/download.html",
        "openroad": "https://openroad.readthedocs.io/en/latest/user/BuildLocally.html",
        "sta": "https://github.com/The-OpenROAD-Project/OpenSTA",
        "magic": "http://opencircuitdesign.com/magic/",
        "netgen": "http://opencircuitdesign.com/netgen/"
    }
    
    report = {"available": [], "missing": []}
    
    for tool, install_url in tools.items():
        if shutil.which(tool):
            report["available"].append(tool)
        else:
            report["missing"].append({
                "tool": tool,
                "install_url": install_url
            })
    
    return _to_text(report)
```

#### 3.5.2 `chipagent_get_tool_requirements`

**用途**: 获取工具的前置要求

```python
@mcp.tool()
def chipagent_get_tool_requirements(tool_name: str) -> str:
    """Get requirements and dependencies for a specific tool.
    
    Returns installation instructions and required system packages.
    """
    requirements = {
        "chipagent_run_synthesis": {
            "tool": "yosys",
            "install": "apt-get install yosys  # Ubuntu\nbrew install yosys  # macOS",
            "version": "0.9+",
            "notes": "Used for RTL synthesis and optimization"
        },
        "chipagent_simulate": {
            "tool": "iverilog",
            "install": "apt-get install iverilog  # Ubuntu\nbrew install icarus-verilog  # macOS",
            "version": "11.0+",
            "notes": "Used for Verilog simulation"
        },
        # ... 其他工具
    }
    
    return _to_text(requirements.get(tool_name, {"error": "Unknown tool"}))
```

---

## 4. 文档更新计划

### 4.1 README.md 更新

**当前**:
```markdown
## 核心能力
- 设计空间探索（DSE）
- 全流程闭环
- 验证仿真
```

**更新后**:
```markdown
## ChipAgent 是什么

ChipAgent 是一个 **EDA 工具的 MCP 封装**，让 LLM (Claude/GPT-4) 可以通过 MCP 协议
调用真实的芯片设计工具。

### ChipAgent 不提供
- ❌ RTL 代码生成（请使用 LLM）
- ❌ 架构设计（请使用 LLM）
- ❌ "估算"或"假数据"

### ChipAgent 提供
- ✅ Lint (Verilator)
- ✅ 仿真 (Icarus Verilog / Verilator)
- ✅ 综合 (Yosys)
- ✅ 时序分析 (OpenSTA)
- ✅ 物理设计 (OpenROAD)
- ✅ 知识库查询

### 正确的工作流

```
用户: "设计一个 RISC-V CPU"
  ↓
Claude: 生成完整 Verilog 代码 (3000+ 行)
  ↓ 调用
ChipAgent lint: 验证语法
  ↓ 调用
ChipAgent simulate: 运行仿真
  ↓ 调用
ChipAgent synthesize: 综合
  ↓
Claude: 根据反馈优化
  ↓ 迭代
最终: 可信的、经过验证的设计
```
```

### 4.2 示例文档

创建 `examples/correct_workflow.py`:

```python
#!/usr/bin/env python3
"""
示例: Claude + ChipAgent 正确工作流

这个示例展示了如何用 Claude 生成代码，用 ChipAgent 验证。
"""

# 步骤 1: Claude 生成 RTL 代码
riscv_code = """
// RISC-V RV32I Single-Cycle Core
module riscv_core (
    input  wire        clk,
    input  wire        rst_n,
    // ... 完整实现 (3000+ 行) ...
);
endmodule
"""

# 步骤 2: 调用 ChipAgent lint
from chipagent.mcp import chipagent_lint
import json

lint_result = chipagent_lint(code=riscv_code)
lint_data = json.loads(lint_result)

if lint_data['status'] == 'error':
    print(f"Lint 错误: {lint_data['issues']}")
    # Claude 根据错误修复代码
else:
    print("✓ Lint 通过")

# 步骤 3: 调用 ChipAgent 仿真
from chipagent.mcp import chipagent_simulate

testbench = """
module tb;
    // ... testbench 代码 ...
endmodule
"""

sim_result = chipagent_simulate(
    reg_code=riscv_code,
    tb_code=testbench,
    coverage=True
)
sim_data = json.loads(sim_result)

if sim_data['status'] == 'PASS':
    print(f"✓ 仿真通过，覆盖率: {sim_data['coverage']}%")
else:
    print(f"✗ 仿真失败: {sim_data['issues']}")

# 步骤 4: 调用 ChipAgent 综合
from chipagent.mcp import chipagent_run_synthesis

synth_result = chipagent_run_synthesis(
    reg_code=riscv_code,
    constraints="create_clock -period 10 [get_ports clk]"
)
synth_data = json.loads(synth_result)

if synth_data['status'] == 'passed':
    print(f"✓ 综合成功")
    print(f"  单元数: {synth_data['cells']}")
    print(f"  面积: {synth_data['area']}")
else:
    print(f"✗ 综合失败: {synth_data['issues']}")
```

### 4.3 安装指南

创建 `INSTALL.md`:

```markdown
# ChipAgent 安装指南

## 基础安装

```bash
pip install -e .
```

## EDA 工具安装

ChipAgent 需要以下 EDA 工具。请根据您的需求安装：

### 必需工具

#### Verilator (Lint + 仿真)
```bash
# Ubuntu/Debian
sudo apt-get install verilator

# macOS
brew install verilator

# 从源码
git clone https://github.com/verilator/verilator
cd verilator
autoconf
./configure
make
sudo make install
```

#### Icarus Verilog (仿真)
```bash
# Ubuntu/Debian
sudo apt-get install iverilog

# macOS
brew install icarus-verilog
```

### 可选工具

#### Yosys (综合)
```bash
# Ubuntu/Debian
sudo apt-get install yosys

# macOS
brew install yosys
```

#### OpenROAD (物理设计)
```bash
# 参考: https://openroad.readthedocs.io/en/latest/user/BuildLocally.html
# 推荐使用 Docker
docker pull openroad/openroad
```

#### OpenSTA (时序分析)
```bash
# 参考: https://github.com/The-OpenROAD-Project/OpenSTA
git clone https://github.com/The-OpenROAD-Project/OpenSTA.git
cd OpenSTA
mkdir build && cd build
cmake ..
make
sudo make install
```

### 检查安装

```bash
python -c "from chipagent.mcp import chipagent_check_tool_availability; print(chipagent_check_tool_availability())"
```

## 常见问题

**Q: 我可以使用 ChipAgent 而不安装任何 EDA 工具吗？**

A: 可以，但很多工具会返回错误。你可以使用:
- 知识库工具 (不需要 EDA)
- 任务管理工具 (不需要 EDA)
- 服务注册工具 (不需要 EDA)

**Q: 为什么 ChipAgent 不提供"估算"结果？**

A: 估算值（如正则表达式计算的时序）毫无意义，会误导用户。
我们宁愿返回错误，也不要返回假数据。
```

---

## 5. 实施计划

### 5.1 Phase 1: 删除和重构 (2-3 天)

**Day 1: 删除工具**
- [ ] 删除 `chipagent_generate_rtl`
- [ ] 删除 `chipagent_run_closed_loop`
- [ ] 更新测试（删除相关测试用例）
- [ ] 运行测试确保无回归

**Day 2: 重构综合工具**
- [ ] 重构 `synth_run.py` (删除 _estimate_synthesis)
- [ ] 重构 `synth_timing.py` (删除 _estimate_timing)
- [ ] 重构 `synth_area.py` (删除 _estimate_area)
- [ ] 重构 `synth_power.py` (删除 _estimate_power)
- [ ] 重构 `synth_formality.py` (删除 _estimate_formality)
- [ ] 更新测试
- [ ] 运行测试

**Day 3: 重构物理设计工具**
- [ ] 重构 `phys_floorplan.py` (删除 _estimate_floorplan)
- [ ] 重构 `phys_placement.py` (删除 _estimate_placement)
- [ ] 重构 `phys_cts.py` (删除 _estimate_cts)
- [ ] 重构 `phys_routing.py` (删除 _estimate_routing)
- [ ] 重构 `phys_drc.py` (删除 _estimate_drc)
- [ ] 重构 `phys_lvs.py` (删除 _estimate_lvs)
- [ ] 更新测试
- [ ] 运行测试

### 5.2 Phase 2: 增强和新增 (2 天)

**Day 4: 增强工具**
- [ ] 增强 `chipagent_lint` (添加参数)
- [ ] 增强 `chipagent_simulate` (添加参数)
- [ ] 增强 `chipagent_elaborate` (添加参数)
- [ ] 更新测试

**Day 5: 新增工具**
- [ ] 添加 `chipagent_check_tool_availability`
- [ ] 添加 `chipagent_get_tool_requirements`
- [ ] 重命名 `chipagent_run_skill` → `chipagent_apply_skill_template`
- [ ] 更新测试
- [ ] 运行完整测试套件

### 5.3 Phase 3: 文档和示例 (1-2 天)

**Day 6: 更新文档**
- [ ] 更新 README.md
- [ ] 更新 CLAUDE_CODE_INTEGRATION.md
- [ ] 创建 INSTALL.md
- [ ] 更新 CLAUDE.md

**Day 7: 创建示例**
- [ ] 创建 `examples/correct_workflow.py`
- [ ] 创建 `examples/riscv_demo/README.md`
- [ ] 更新 `examples/README.md`

### 5.4 Phase 4: 验证和发布 (1 天)

**Day 8: 最终验证**
- [ ] 运行完整测试套件
- [ ] 验证 MCP 工具清单
- [ ] 验证文档链接
- [ ] 创建 CHANGELOG.md
- [ ] 发布新版本

---

## 6. 验证计划

### 6.1 测试用例

#### 6.1.1 删除的工具测试
```python
def test_generate_rtl_removed():
    """验证 chipagent_generate_rtl 已被删除"""
    from chipagent.mcp import mcp
    tools = list(mcp._tool_manager._tools.keys())
    assert "chipagent_generate_rtl" not in tools

def test_run_closed_loop_removed():
    """验证 chipagent_run_closed_loop 已被删除"""
    from chipagent.mcp import mcp
    tools = list(mcp._tool_manager._tools.keys())
    assert "chipagent_run_closed_loop" not in tools
```

#### 6.1.2 重构的工具测试
```python
def test_synthesis_requires_yosys():
    """验证综合工具需要 yosys"""
    from chipagent.tools.synth_run import SynthesisTool
    from chipagent.tools.base import ToolContext
    
    # 模拟 yosys 不存在
    import shutil
    original_which = shutil.which
    shutil.which = lambda x: None if x == "yosys" else original_which(x)
    
    try:
        result = SynthesisTool().run(ToolContext(inputs={"reg_code": "module top; endmodule"}))
        assert result.result["status"] == "error"
        assert "yosys" in result.result["message"].lower()
    finally:
        shutil.which = original_which

def test_timing_requires_sta():
    """验证时序分析需要 OpenSTA"""
    # 类似测试
    pass
```

#### 6.1.3 新增工具测试
```python
def test_check_tool_availability():
    """验证工具可用性检查"""
    from chipagent.mcp import chipagent_check_tool_availability
    import json
    
    result = chipagent_check_tool_availability()
    data = json.loads(result)
    
    assert "available" in data
    assert "missing" in data
    assert isinstance(data["available"], list)
    assert isinstance(data["missing"], list)
```

### 6.2 集成测试

```python
def test_correct_workflow():
    """验证正确的工作流"""
    from chipagent.mcp import (
        chipagent_lint,
        chipagent_simulate,
        chipagent_run_synthesis
    )
    import json
    
    # 步骤 1: Lint
    rtl = "module top(input clk, output reg q); always @(posedge clk) q <= ~q; endmodule"
    lint = json.loads(chipagent_lint(code=rtl))
    assert lint["status"] in ["passed", "error"]
    
    # 步骤 2: 仿真
    tb = "module tb; reg clk; initial begin clk=0; forever #5 clk=~clk; end top dut(.clk(clk)); endmodule"
    sim = json.loads(chipagent_simulate(reg_code=rtl, tb_code=tb))
    assert sim["status"] in ["PASS", "FAIL", "error"]
    
    # 步骤 3: 综合 (如果没有 yosys 会返回 error)
    synth = json.loads(chipagent_run_synthesis(reg_code=rtl))
    assert synth["status"] in ["passed", "error"]
```

---

## 7. 预期结果

### 7.1 重构前 vs 重构后

| 指标 | 重构前 | 重构后 |
|------|--------|--------|
| 工具总数 | 45 | ~32 |
| 返回假数据的工具 | 13 (29%) | 0 (0%) |
| 需要真实 EDA 的工具 | 13 | 13 |
| 不依赖 EDA 的工具 | 32 | 19 |
| 用户信任度 | ❌ 低 | ✅ 高 |
| 文档清晰度 | ⚠️ 模糊 | ✅ 清晰 |

### 7.2 用户体验改进

**重构前**:
```
用户: "设计 RISC-V CPU"
ChipAgent: 生成 8-bit 寄存器 (完全不匹配)
用户: 😡 这是什么垃圾？
```

**重构后**:
```
用户: "设计 RISC-V CPU" (对 Claude 说)
Claude: 生成 3000 行完整实现
  ↓ 调用 ChipAgent
ChipAgent lint: ✓ 通过
  ↓ 调用
ChipAgent simulate: ✓ 通过
  ↓ 调用
ChipAgent synthesize: ✓ 成功，资源使用 45%
用户: 😊 太棒了！
```

---

## 8. 风险和缓解

### 8.1 风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 用户抱怨工具报错（没有 EDA） | 中 | 高 | 提供清晰的安装指南和 Docker 镜像 |
| 删除工具导致回归 | 高 | 低 | 完整的测试覆盖 |
| 文档更新不及时 | 中 | 中 | 文档更新作为验收标准 |
| LLM 生成的代码质量不稳定 | 中 | 中 | 强调验证工具的重要性 |

### 8.2 缓解措施

1. **提供 Docker 镜像**:
   ```bash
   docker pull chipagent/eda-tools:latest
   docker run -v $(pwd):/work chipagent/eda-tools chipagent_lint code.v
   ```

2. **渐进式重构**:
   - 先删除最明显的假工具 (generate_rtl)
   - 再重构综合工具
   - 最后重构物理设计工具

3. **用户沟通**:
   - 发布博客文章解释架构变更
   - 更新 README 强调正确用法
   - 提供迁移指南

---

## 9. 总结

### 9.1 核心变更

1. **删除 2 个工具**: `generate_rtl`, `run_closed_loop`
2. **重构 13 个工具**: 综合 + 物理设计工具（删除假数据）
3. **增强 2 个工具**: `lint`, `simulate`
4. **新增 2 个工具**: `check_tool_availability`, `get_tool_requirements`
5. **重命名 1 个工具**: `run_skill` → `apply_skill_template`

### 9.2 时间线

- **Day 1-3**: 删除和重构 (综合 + 物理设计)
- **Day 4-5**: 增强和新增工具
- **Day 6-7**: 文档和示例
- **Day 8**: 验证和发布

**总计**: 8 天

### 9.3 成功标准

- ✅ 所有测试通过 (159+ passed)
- ✅ 没有工具返回假数据
- ✅ 文档清晰说明正确用法
- ✅ 用户可以用 LLM + ChipAgent 完成真实设计

---

## 10. 下一步

完成这次重构后，后续可以：

1. **添加更多真实工具**:
   - SymbiYosys (形式验证)
   - Verilator coverage (覆盖率分析)
   - Magic (DRC/LVS)

2. **改进工具接口**:
   - 支持更多参数
   - 返回更详细的报告
   - 支持增量验证

3. **建立 LLM 协作生态**:
   - 提供 Claude/GPT-4 prompt 模板
   - 创建最佳实践文档
   - 收集用户案例

4. **企业级功能**:
   - 多租户支持
   - 工具使用统计
   - 设计版本管理
