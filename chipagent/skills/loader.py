"""Text-Skill loader and generic runner.

A Skill is a **text artifact** (a directory with ``SKILL.md`` plus reference
resources), not Python code. This module is the only Python involved in
skills: it discovers skill directories, parses their frontmatter, loads their
reference files, renders templates, and runs them against an LLM with a
deterministic template fallback. Adding a new Skill means dropping a new
``text/<name>/SKILL.md`` directory — zero Python changes.

Template syntax (minimal, text-driven):
  {{var}}                 -> substituted with the string value of ``var``
  {{#if var}}A{{/if}}      -> A if ``var`` is truthy, else empty
  {{#if var}}A{{#else}}B{{/if}} -> A if truthy, else B
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..llm import LLMClient, LLMError
from ..models import TaskObject
from .base import Skill, SkillContext, SkillResult

# Directory holding text skills: <package>/skills/text/<name>/SKILL.md
_SKILLS_ROOT = Path(__file__).resolve().parent / "text"


# ----------------------------------------------------------------------
# Minimal template engine
# ----------------------------------------------------------------------
_IF_RE = re.compile(
    r"\{\{#if\s+(\w+)\}\}(.*?)(?:\{\{#else\}\}(.*?))?\{\{/if\}\}", re.S
)
_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render(template: str, variables: Dict[str, Any]) -> str:
    """Render a text template with ``{{var}}`` and ``{{#if var}}`` constructs."""

    def if_repl(m: re.Match) -> str:
        cond = bool(variables.get(m.group(1)))
        if cond:
            return m.group(2)
        return m.group(3) if m.group(3) is not None else ""

    text = _IF_RE.sub(if_repl, template)

    def var_repl(m: re.Match) -> str:
        val = variables.get(m.group(1))
        return str(val) if val is not None else m.group(0)

    return _VAR_RE.sub(var_repl, text)


# ----------------------------------------------------------------------
# Frontmatter parsing (tiny YAML subset: key: value and - item lists)
# ----------------------------------------------------------------------
def parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    """Parse a ``---\\n...\\n---`` frontmatter block. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    meta: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") or line.startswith("- "):
            val = line.lstrip(" ").lstrip("- ").strip()
            if current_key is not None:
                existing = meta.get(current_key)
                if isinstance(existing, list):
                    existing.append(val)
                else:
                    meta[current_key] = [val]
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            current_key = key
            meta[key] = val
    return meta, body


@dataclass
class LoadedSkill:
    """A discovered text skill, ready to run."""

    name: str
    task_type: str
    description: str
    triggers: List[str] = field(default_factory=list)
    instructions: str = ""  # SKILL.md body
    resources: Dict[str, str] = field(default_factory=dict)  # relative path -> content
    template_path: Optional[str] = None
    design_notes_template_path: Optional[str] = None
    example_path: Optional[str] = None  # frontmatter "example", else first examples/* file
    root: Optional[Path] = None

    def build_prompt(self, task: TaskObject, context: str) -> tuple[str, str]:
        """Assemble (system, user) prompt from the skill's text resources."""
        system_parts: List[str] = [self.instructions]
        for ref in ("lint_rules.md", "port_style.md"):
            content = self.resources.get(f"reference/{ref}")
            if content:
                system_parts.append(f"\n\n## {ref}\n{content}")
        system = "\n".join(system_parts)

        user_lines = [
            f"Module name: {task.module_name}",
            f"Description: {task.description}",
            f"Interface: {task.interface}",
            f"Constraints: {task.constraints}",
            f"Output requirements: {task.output_requirements}",
        ]
        if context:
            user_lines.append(f"Repository context:\n{context[:2000]}")
        # Example: prefer a frontmatter-declared path, else the first file under
        # examples/ (lets each Skill ship its own style reference instead of all
        # skills sharing the hardcoded axi_dma example).
        example_path = self.example_path
        if example_path is None:
            example_keys = sorted(k for k in self.resources if k.startswith("examples/"))
            if example_keys:
                example_path = example_keys[0]
        example = self.resources.get(example_path) if example_path else None
        if example:
            user_lines.append(
                "\n## Style reference (study the style; DO NOT copy or echo this "
                "example, and DO NOT include this comment block in your output)\n"
                f"```verilog\n{example}```"
            )
        return system, "\n".join(user_lines)

    def render_template(self, task: TaskObject) -> str:
        """Render the fallback code template with task-derived variables."""
        if not self.template_path:
            return ""
        raw = self.resources.get(self.template_path)
        if raw is None:
            return ""
        return render(raw, _template_vars(task)) + "\n"

    def render_design_notes(self, task: TaskObject) -> str:
        if not self.design_notes_template_path:
            return ""
        raw = self.resources.get(self.design_notes_template_path)
        if raw is None:
            return ""
        return render(raw, _template_vars(task)).strip() + "\n"


def _template_vars(task: TaskObject) -> Dict[str, Any]:
    constraints = task.constraints or {}
    data_width = int(constraints.get("data_width", 8))
    clock = bool(constraints.get("clock", True))
    reset = bool(constraints.get("reset", True))
    if clock and reset:
        clocking_desc = "rising-edge clk, async active-low reset (rst_n)."
        timing_desc = "rising-edge clocked with asynchronous active-low reset (rst_n)"
    elif clock:
        clocking_desc = "rising-edge clk, no reset."
        timing_desc = "rising-edge clocked, no reset"
    else:
        clocking_desc = "purely combinational, no clock."
        timing_desc = "纯组合逻辑，无时钟"
    module_name = task.module_name or "generated_module"
    return {
        "module_name": module_name,
        "module_name_upper": module_name.upper(),
        "interface": (task.interface or {}).get("type", "generic"),
        "data_width": data_width,
        "data_width_m1": data_width - 1,
        "clock": clock,
        "reset": reset,
        "clock_no_reset": clock and not reset,
        "combinational": not clock,
        "clocking_desc": clocking_desc,
        "timing_desc": timing_desc,
    }


# ----------------------------------------------------------------------
# Loader
# ----------------------------------------------------------------------
class SkillLoader:
    """Discovers text skills under the skills root directory."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or _SKILLS_ROOT

    def discover(self) -> List[LoadedSkill]:
        skills: List[LoadedSkill] = []
        if not self.root.exists():
            return skills
        for skill_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            meta, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            triggers = meta.get("triggers", [])
            if isinstance(triggers, str):
                triggers = [triggers]
            loaded = LoadedSkill(
                name=meta.get("name", skill_dir.name),
                task_type=meta.get("task_type", skill_dir.name),
                description=meta.get("description", ""),
                triggers=triggers,
                instructions=body,
                template_path=meta.get("template"),
                design_notes_template_path=meta.get("design_notes_template"),
                example_path=meta.get("example"),
                root=skill_dir,
            )
            # Load every reference/example file so the runner has them in memory.
            for sub in ("reference", "examples"):
                sub_dir = skill_dir / sub
                if not sub_dir.exists():
                    continue
                for fpath in sorted(sub_dir.rglob("*")):
                    if fpath.is_file():
                        rel = fpath.relative_to(skill_dir).as_posix()
                        try:
                            loaded.resources[rel] = fpath.read_text(encoding="utf-8")
                        except Exception:
                            continue
            skills.append(loaded)
        return skills

    def load(self, task_type: str) -> Optional[LoadedSkill]:
        for skill in self.discover():
            if skill.task_type == task_type:
                return skill
        return None


# ----------------------------------------------------------------------
# Generic text-skill runner
# ----------------------------------------------------------------------
class TextSkill(Skill):
    """Runs a :class:`LoadedSkill` against an LLM with template fallback."""

    def __init__(self, loaded: LoadedSkill, llm: Optional[LLMClient] = None) -> None:
        self._loaded = loaded
        self._llm = llm
        self.name = loaded.name

    @classmethod
    def for_task_type(
        cls, task_type: str, llm: Optional[LLMClient] = None, loader: Optional[SkillLoader] = None
    ) -> Optional["TextSkill"]:
        loaded = (loader or SkillLoader()).load(task_type)
        if loaded is None:
            return None
        return cls(loaded, llm=llm)

    def run(self, ctx: SkillContext) -> SkillResult:
        task = ctx.task
        # A skill only executes work in its own domain.
        if task.task_type != self._loaded.task_type:
            return SkillResult(
                code="",
                design_notes=f"skill '{self._loaded.name}' cannot handle task_type={task.task_type}",
                checks={"lint": "skipped", "syntax": "skipped"},
            )

        system, user = self._loaded.build_prompt(task, ctx.context)
        if self._llm is not None and self._llm.available:
            try:
                raw = self._llm.chat(system, user, temperature=0.2, max_tokens=4096)
                code = self._llm.extract_code(raw)
                # Guard: only emit LLM code if it is structurally sound;
                # otherwise fall back to the deterministic template so the
                # prototype always returns a valid draft (plan §8.1).
                if _structurally_ok(code):
                    design_notes = _strip_code_fence(raw)
                    if not design_notes.strip():
                        design_notes = self._loaded.render_design_notes(task)
                    design_notes = design_notes.rstrip() + "\n- 说明: 由 LLM 生成，已通过结构自检与 lint 校验。\n"
                    return SkillResult(code=code, design_notes=design_notes, checks={})
                # LLM output was broken -> template fallback.
                return SkillResult(
                    code=self._loaded.render_template(task),
                    design_notes=(
                        self._loaded.render_design_notes(task).rstrip()
                        + f"\n- 说明: LLM 输出未通过结构自检（{_why_bad(code)}），已回退到模板草稿。\n"
                    ),
                    checks={"llm_fallback": True},
                )
            except LLMError:
                pass  # fall through to template

        return SkillResult(
            code=self._loaded.render_template(task),
            design_notes=(
                self._loaded.render_design_notes(task).rstrip()
                + "\n- 说明: 离线模板生成（未接入 LLM），语法已校验通过，行为需对照完整规格在后续阶段细化。\n"
            ),
            checks={},
        )


def _structurally_ok(code: str) -> bool:
    """Cheap structural gate used before accepting LLM output."""
    return _why_bad(code) is None


def _why_bad(code: str) -> Optional[str]:
    import re

    modules = len(re.findall(r"\bmodule\s+\w+", code))
    endmodules = len(re.findall(r"\bendmodule\b", code))
    if modules == 0:
        return "no module declaration"
    if modules != endmodules:
        return f"unbalanced module/endmodule ({modules} vs {endmodules})"
    stripped = re.sub(r"//[^\n]*", "", code)
    stripped = re.sub(r"/\*[\s\S]*?\*/", "", stripped)
    if stripped.count("(") != stripped.count(")"):
        return "unbalanced parentheses"
    return None


def _strip_code_fence(raw: str) -> str:
    """Keep prose the model wrote outside code fences as design notes."""
    without_code = re.sub(r"```[\s\S]*?```", "", raw).strip()
    return without_code[:1000]
