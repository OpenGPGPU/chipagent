# ChipAgent Architecture v3.0

## Current Position

ChipAgent is an EDA tool MCP service layer for LLM-assisted chip design.

It is not the primary AI chip designer. Claude Code or another LLM is expected
to understand requirements, design architecture, write RTL/software, and decide
how to iterate. ChipAgent provides reliable backend capabilities through MCP:
tool execution, verification, measurement, knowledge lookup, task tracking, and
audit-friendly result reporting.

## Why v3 Exists

The original v2 design described ChipAgent as a broad AI chip development agent:
natural-language entry, code generation, LangGraph planning, Temporal execution,
multi-agent collaboration, and EDA tooling in one platform.

Implementation experience showed that this was too broad for a trustworthy
engineering tool. Complex architecture and RTL generation are better handled by
the caller LLM. ChipAgent should focus on results that can be checked, repeated,
and trusted.

## Responsibility Split

| Layer | Owns |
|---|---|
| Claude Code / LLM | Requirement understanding, architecture design, RTL/software generation, optimization decisions |
| ChipAgent | MCP tools, EDA command execution, simulation, synthesis, timing, physical checks, PPA estimates, knowledge lookup, task/audit metadata |
| EDA tools | Verilator, Icarus Verilog, Yosys, OpenSTA, OpenROAD, Magic, Netgen, and related command-line tools |

## Non-Goals

- ChipAgent should not present itself as a complete autonomous chip designer.
- ChipAgent should not claim complex RTL generation as a production capability.
- ChipAgent should not return fabricated EDA results when a required tool is
  missing.
- ChipAgent should not expand tool count at the expense of result trust.

## Result Trust Contract

Every MCP tool should make the source of its result explicit:

```json
{
  "status": "passed | failed | error | unavailable | estimated",
  "source": "tool | structural_check | static_estimate | template",
  "tool": "yosys",
  "tool_available": true,
  "command": "yosys -s synth.ys",
  "artifacts": {},
  "issues": []
}
```

Rules:

1. If a real EDA tool is required and unavailable, return `unavailable` or
   `error`; do not synthesize fake success data.
2. Static PPA helpers may return estimates, but must label them as estimates.
3. Template-based skills may remain for scaffolding, but must not be documented
   as complex design generation.
4. Tool outputs should include enough metadata for an LLM or engineer to decide
   whether the result is actionable.

## Current Development Priorities

1. Align documentation with this v3 position.
2. Normalize tool result schemas around the trust contract.
3. Make real EDA tool availability visible through health/discovery tools.
4. Keep generation-oriented skills as scaffolding helpers, not core product
   promises.
5. Rework DSE so it evaluates candidate designs supplied by the LLM/user rather
   than pretending to be the design brain.
6. Use Temporal, task panel, and multi-agent code only where they improve long
   running tool execution, approval, recovery, or auditability.

## Document Status

This document is the current architecture source of truth.

Older phase documents and v2 design files are historical references only. They
may describe goals that were intentionally narrowed or replaced by this v3
architecture.
