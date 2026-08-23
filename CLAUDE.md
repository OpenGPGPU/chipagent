# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## What is ChipAgent

ChipAgent is a **chip design EDA tool MCP service layer** with **53 MCP tools** — it lets Claude Code
call real chip design tools (Yosys, OpenROAD, Verilator, etc.) via MCP protocol for automated
verification of chip design flows.

The current architecture source of truth is `ChipAgent_Architecture_v3.0.md`.
Older v2/phase documents are historical references and may describe goals that
were intentionally narrowed.

**Important**: ChipAgent is not an "AI chip designer" but an "EDA tool service layer".
- ✅ ChipAgent: wraps real EDA tools, provides verification capabilities
- ✅ Claude: understands requirements, generates code, iterates optimization
- ❌ ChipAgent does not: own production code generation or architecture decisions
- ❌ ChipAgent should not return fabricated EDA success data when required tools
  are missing

Architecture: Claude Code is the reasoning layer; chipagent is the backend for measurement + validation.

## Quick Commands

```bash
pip install -e .                    # install
pytest                              # run all tests (183 passed / 3 skipped)
pytest tests/test_phase3.py -v      # Phase 3 tests (27 tests)
pytest tests/test_chat_mcp.py       # MCP tool + intent routing tests
python -m chipagent.mcp --list      # print MCP tool catalogue (53 tools)
python -m chipagent.mcp             # start MCP server (stdio)
python -m chipagent.toolchain --pretty  # inspect EDA tool availability
bash scripts/setup_eda_env.sh --docker  # build/configure the default Docker toolchain images
bash scripts/setup_eda_env.sh --smoke   # smoke-test configured Docker toolchain images
# If Docker Hub pulls fail but ubuntu:latest is cached:
CHIPAGENT_BASE_IMAGE=ubuntu:latest bash scripts/setup_eda_env.sh --docker
# Optional heavy OpenROAD/OpenSTA adapter:
CHIPAGENT_OPENROAD_BASE_IMAGE=<image-with-openroad-and-sta> bash scripts/setup_eda_env.sh --docker-openroad
bash scripts/setup_eda_env.sh --smoke-openroad
```

EDA tools are resolved from PATH/known local envs first; when a supported host
tool is missing, ChipAgent automatically selects the configured Docker image.
Base tools use `chipagent/tools:latest`; OpenROAD/OpenSTA use
`chipagent/openroad:latest` via `CHIPAGENT_OPENROAD_IMAGE`.

## Project Structure

```
chipagent/
  mcp.py                # MCP server entry point (FastMCP, stdio transport, 53 tools)
  workflow.py           # LangGraph orchestrator + CLI entry (run_workflow)
  multistep.py          # Multi-step workflow: reg→rtl→tb→sim→driver→align (deprecated, use Claude)
  parser.py             # NL → TaskObject parser (rules + LLM hybrid)
  models.py             # Core data models (TaskObject, TaskStatus, etc.)
  llm.py                # LLM client (Anthropic preferred, OpenAI fallback)
  interaction.py        # Friendly CLI with formatted output
  chat/                 # Conversational agent (ChatSession, multi-turn + streaming)
  dse/                  # Design-Space Exploration (runner/proposer/measurer/scorer)
  skills/               # Text-based skill framework (SkillLoader + TextSkill)
    text/               # 7 skills: rtl_generation, reg_definition, register_header,
                        #          hal_library, linux_driver, testbench_generation, uvm_skeleton
  tools/                # 20 Python tools: simulation, alignment, elaborate, interface, coverage,
                        #   cosim, synthesis (5), physical_design (6), dpi_cosim, ppa (4)
    ppa/                # PPA estimation: area, performance, power, target checking
  knowledge/            # Knowledge base (TF-IDF + BM25 hybrid retrieval)
  agents/               # Multi-agent coordination (Hardware/Verification/Software agents)
  registry/             # Service registry (tool metadata, discovery, routing)
  router/               # Task router (routes requests to appropriate tools)
  security/             # Security module (API key auth, RBAC, audit logs)
  temporal_runtime.py   # Temporal workflow integration
  taskpanel.py          # Task panel: submit/approve/rollback
  audit.py              # JSONL audit log + artifact provenance
  validation.py         # VerilogLinter (structural lint)
  sandbox.py            # Sandboxed execution helpers
  repository.py         # Artifact persistence + repo context loading
  state.py              # TaskStateMachine
tests/                  # 9 test files (phase1/phase2/phase3/chat_mcp/dse/skills/register/layers/temporal)
generated/              # Output directory for generated artifacts
logs/                   # Execution + DSE audit logs
.mcp.json               # MCP server registration (project-level, auto-discovered by Claude Code)
```

## Key Design Decisions

- **MCP tools run with LLM off** (`CHIPAGENT_DISABLE_LLM=1`): Claude Code is the brain.
  Template skills are scaffolding helpers only; do not present them as complex
  RTL generation.
- **Skills are pure text directories**: `SKILL.md` + `reference/` + `examples/` — zero
  Python code needed to add a skill. The `SkillLoader` discovers and renders templates.
- **Tools are Python runners**: wrap external EDA tools (verilator/iverilog/yosys).
  Tools that need a real EDA backend should report `unavailable` or `error`
  when that backend is missing. Static PPA helpers may estimate, but must label
  results as estimates.
- **Workflow dispatch**: `run_workflow()` routes based on task type — `hw_sw_codesign`
  goes to `run_multistep()`, others go to single-skill LangGraph orchestrator. If
  `CHIPAGENT_USE_TEMPORAL=1`, dispatches to Temporal workflows instead.

## Adding a New Skill

1. Create `chipagent/skills/text/<skill_name>/SKILL.md` with YAML frontmatter:
   ```yaml
   ---
   task_type: <type>
   name: <name>
   description: <one-line>
   triggers: ["keyword1", "keyword2"]
   ---
   ```
2. Add `reference/` (templates, lint rules, design notes) and `examples/` directories.
3. The `SkillLoader` auto-discovers it. Call via `chipagent_run_skill` MCP tool.

## Adding a New Tool

1. Create a class in `chipagent/tools/` extending `Tool` (from `tools/base.py`).
2. Implement `name`, `description`, `input_schema`, and `run(context: ToolContext) -> ToolResult`.
3. The `ToolLoader` auto-discovers it. Optionally wrap it as an MCP tool in `mcp.py`.

## Testing

- `conftest.py` forces `CHIPAGENT_DISABLE_LLM=1` for deterministic offline testing.
- Online LLM streaming tests are skipped in pytest offline mode.
- EDA-dependent tests gracefully degrade when verilator/iverilog/yosys aren't installed.
