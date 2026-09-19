# Agent Contribution Rules

When creating a Git commit, include a `Co-authored-by` trailer for the agent. The trailer must identify the agent and include the model name, for example:

```text
Co-authored-by: Codex (GPT-5) <codex@openai.com>
```

---

## ChipAgent Project Guidelines

### Code Style

- **Python**: Follow PEP 8, use type hints for all public functions
- **No comments** unless explicitly requested — code should be self-documenting
- **Imports**: Group stdlib, third-party, local; alphabetize within groups
- **Async**: Use `async`/`await` for I/O-bound operations

### Testing

- All new tools must have tests in `tests/`
- Run full test suite before committing: `pytest`
- Target: 0 regressions (currently 232 passed, 5 skipped)
- EDA-dependent tests should gracefully degrade when tools unavailable

### Adding a New MCP Tool

1. Create tool class in `chipagent/tools/` extending `Tool` (from `tools/base.py`)
2. Implement: `name`, `description`, `input_schema`, `run(context: ToolContext) -> ToolResult`
3. ToolLoader auto-discovers; optionally wrap as MCP tool in `mcp.py`
4. **Result trust contract**: Every tool must return explicit `source` field:
   ```json
   { "status": "passed|failed|error|unavailable|estimated", "source": "tool|structural_check|static_estimate|template" }
   ```
5. If real EDA tool required and unavailable → return `unavailable` or `error` (never fake data)

### Adding a New Skill

1. Create `chipagent/skills/text/<skill_name>/SKILL.md` with YAML frontmatter:
   ```yaml
   ---
   task_type: <type>
   name: <name>
   description: <one-line>
   triggers: ["keyword1", "keyword2"]
   ---
   ```
2. Add `reference/` (templates, lint rules, design notes) and `examples/` directories
3. SkillLoader auto-discovers; call via `chipagent_run_skill` MCP tool

### Commit Conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- One logical change per commit
- Include `Co-authored-by` trailer for agent contributions

### Documentation

- Update relevant `.md` files when changing user-facing behavior
- Keep `README.md`, `INSTALL.md`, `examples/README.md` in sync
- `ChipAgent_Architecture_v3.0.md` is the architecture source of truth

### Lint/Typecheck

```bash
# Type checking (if mypy configured)
# mypy chipagent/

# Linting (if ruff/flake8 configured)
# ruff check chipagent/
```

### Security

- Never commit secrets, API keys, or credentials
- Docker sandbox defaults to fail-closed
- `CHIPAGENT_ALLOW_HOST_FALLBACK=1` only for trusted local inputs