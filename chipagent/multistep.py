"""Multi-step co-design orchestrator (Phase 2 Step 3).

Chains the design → verify → software closed loop in one LangGraph:
``parse → load_context → gen_reg_block → gen_header → gen_testbench →
run_simulation → gen_driver → align_check → persist → finalize``.

Each generation step runs the text Skill matching that step's ``task_type``
(discovered by :class:`SkillLoader`), threading the upstream artifact (RTL /
header) into the next Skill's context. Tools (simulation, alignment) come
from :class:`ToolLoader`. The public entry :func:`run_multistep` returns the
same result shape as the single-skill :func:`chipagent.workflow.run_workflow`,
so the CLI and Temporal dispatch stay uniform.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .llm import LLMClient
from .logging_utils import append_log
from .models import TaskObject, TaskStatus
from .parser import TaskParser
from .repository import Repository
from .skills import SkillContext, SkillLoader, TextSkill
from .skills.base import Skill
from .state import TaskStateMachine
from .tools import ToolContext, ToolLoader
from .tools.base import Tool
from .validation import VerilogLinter


class MultiGraphState(TypedDict, total=False):
    request: str
    task: Dict[str, Any]
    context: str
    reg_code: str
    header_code: str
    hal_code: str
    tb_code: str
    driver_code: str
    sim_result: Dict[str, Any]
    alignment_result: Dict[str, Any]
    checks: Dict[str, Any]
    logs: Annotated[List[Dict[str, Any]], operator.add]
    status: str
    artifacts: Dict[str, Any]
    output_dir: Optional[str]
    context_path: Optional[str]
    context_dir: Optional[str]
    gate_failed: bool
    approval: str
    error: Optional[str]


# A human-in-the-loop approval callback. Returns "approve" | "reject" | "modify".
# Injected by the task panel (terminal prompt) or by tests; the default refuses.
Approver = Any


def _default_approver(detail: str) -> str:
    """Default approver: refuse (so approval is always explicit)."""
    return "reject"


# The ordered generation steps: (step_name, skill task_type, upstream_field_for_context)
# Phase 2 §4.3/§6 Step 5: hal_library sits between the register header and the
# Linux driver on the software branch — it consumes the header (offset/mask
# defines) and the driver in turn consumes the header.
_STEPS = [
    ("gen_reg_block", "reg_definition", None),
    ("gen_header", "register_header", "reg_code"),
    ("gen_hal", "hal_library", "header_code"),
    ("gen_testbench", "testbench_generation", "reg_code"),
    ("gen_driver", "linux_driver", "header_code"),
]


class MultiStepOrchestrator:
    """Owns the collaborators for the multi-step closed loop."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        llm: Optional[LLMClient] = None,
        parser: Optional[TaskParser] = None,
        repository: Optional[Repository] = None,
        linter: Optional[VerilogLinter] = None,
        skills: Optional[List[Skill]] = None,
        tools: Optional[List[Tool]] = None,
        use_llm: bool = True,
        require_approval: bool = False,
        approver: Optional["Approver"] = None,
    ) -> None:
        self.settings = settings or Settings.load()
        self.llm = llm or LLMClient(self.settings.llm)
        self.parser = parser or TaskParser(self.llm, use_llm=use_llm)
        self.repository = repository or Repository(
            repo_root=self.settings.repo_root, use_git=self.settings.use_git
        )
        self.linter = linter or VerilogLinter()
        self.skills: List[Skill] = skills or [
            TextSkill(loaded, llm=self.llm if use_llm else None)
            for loaded in SkillLoader().discover()
        ]
        self.tools: List[Tool] = tools or ToolLoader().discover()
        self._use_llm = use_llm
        # Phase 2 §4.6: human-in-the-loop approval gate. When
        # ``require_approval`` is set and the align/sim gate fails, the
        # orchestrator consults ``approver`` before persisting — so an
        # unapproved artifact never lands on disk. Default approver refuses
        # (returns "reject"), so approval is opt-in and explicit.
        self.require_approval = require_approval
        self.approver: "Approver" = approver or _default_approver

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _skill(self, task_type: str) -> Optional[Skill]:
        for s in self.skills:
            if s.name == task_type:
                return s
        return None

    def _tool(self, name: str) -> Optional[Tool]:
        for t in self.tools:
            if t.name == name:
                return t
        return None

    def _step_task(self, base: Dict[str, Any], task_type: str) -> TaskObject:
        """Build a per-step TaskObject carrying the parsed module + constraints,
        defaulting the timing constraints the templates rely on."""
        constraints = dict(base.get("constraints") or {})
        constraints.setdefault("clock", True)
        constraints.setdefault("reset", True)
        constraints.setdefault("data_width", 32)
        return TaskObject(
            task_type=task_type,
            module_name=base.get("module_name"),
            description=base.get("description", ""),
            interface=base.get("interface", {}) or {},
            constraints=constraints,
            output_requirements=base.get("output_requirements", {}) or {},
        )

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _node_parse(self, state: MultiGraphState) -> Dict[str, Any]:
        task = self.parser.parse(state["request"]).to_dict()
        return {"task": task, "status": TaskStatus.RUNNING.value,
                "logs": [append_log([], "parse_request", "completed", task)]}

    def _node_load_context(self, state: MultiGraphState) -> Dict[str, Any]:
        context = self.repository.load_context(
            context_path=state.get("context_path"),
            context_dir=state.get("context_dir"),
        )
        update: Dict[str, Any] = {"context": context}
        if context:
            update["logs"] = [{"step": "load_context", "status": "completed",
                               "detail": context[:200]}]
        return update

    def _make_gen_node(self, step_name: str, task_type: str, upstream_field: Optional[str]):
        def node(state: MultiGraphState) -> Dict[str, Any]:
            base = state["task"]
            skill = self._skill(task_type)
            if skill is None:
                return {"logs": [append_log([], step_name, "failed",
                                            f"no skill for {task_type}")]}
            upstream = ""
            if upstream_field:
                upstream = state.get(upstream_field, "")
            ctx = SkillContext(
                task=self._step_task(base, task_type),
                context=upstream,
                llm=self.llm if self._use_llm else None,
            )
            result = skill.run(ctx)
            code = result.code
            # First-pass structural lint on generated code (skipped for C files).
            if task_type in ("reg_definition", "testbench_generation") and code:
                lint = self.linter.run(code)
                if lint["lint"] != "passed":
                    return {upstream_field or "code": code if upstream_field else "",
                            "logs": [append_log([], step_name, "completed",
                                                {"lint": lint["lint"], "issues": lint["issues"]})]}
            field_map = {
                "gen_reg_block": "reg_code",
                "gen_header": "header_code",
                "gen_hal": "hal_code",
                "gen_testbench": "tb_code",
                "gen_driver": "driver_code",
            }
            out_field = field_map[step_name]
            return {out_field: code,
                    "logs": [append_log([], step_name, "completed",
                                        f"{len(code)} chars generated")]}
        return node

    def _node_run_sim(self, state: MultiGraphState) -> Dict[str, Any]:
        tool = self._tool("run_simulation")
        if tool is None:
            return {"sim_result": {"passed": "skipped", "reason": "no sim tool"},
                    "logs": [append_log([], "run_simulation", "skipped", "no tool")]}
        ctx = ToolContext(
            task=self._step_task(state["task"], "hw_sw_codesign"),
            inputs={"reg_code": state.get("reg_code", ""), "tb_code": state.get("tb_code", "")},
            settings=self.settings,
        )
        result = tool.run(ctx)
        return {"sim_result": result.result,
                "logs": [append_log([], "run_simulation", "completed", result.result)]}

    def _node_align_check(self, state: MultiGraphState) -> Dict[str, Any]:
        tool = self._tool("check_register_alignment")
        if tool is None:
            return {"alignment_result": {"aligned": False, "reason": "no align tool"},
                    "logs": [append_log([], "align_check", "skipped", "no tool")]}
        ctx = ToolContext(
            task=self._step_task(state["task"], "hw_sw_codesign"),
            inputs={"reg_code": state.get("reg_code", ""), "header_code": state.get("header_code", "")},
            settings=self.settings,
        )
        result = tool.run(ctx)
        aligned = bool(result.result.get("aligned"))
        return {
            "alignment_result": result.result,
            "gate_failed": not aligned,
            "logs": [append_log([], "align_check", "completed", result.result)],
        }

    def _node_approval_gate(self, state: MultiGraphState) -> Dict[str, Any]:
        """Phase 2 §4.6 human-in-the-loop gate.

        When a verification gate failed and approval is required, consult the
        approver; an unapproved artifact is **not** persisted. When approval is
        not required the gate is a passthrough.
        """
        sim = state.get("sim_result", {})
        align = state.get("alignment_result", {})
        gate_failed = bool(state.get("gate_failed")) or sim.get("passed") == "failed"
        if not gate_failed:
            return {"approval": "approve",
                    "logs": [append_log([], "approval_gate", "auto-approved", "gates passed")]}
        if not self.require_approval:
            return {"approval": "approve",
                    "logs": [append_log([], "approval_gate", "auto-approved", "approval not required")]}
        detail = f"sim={sim.get('passed')} aligned={align.get('aligned')}"
        decision = self.approver(detail)
        return {"approval": decision,
                "logs": [append_log([], "approval_gate", decision, detail)]}

    def _node_persist(self, state: MultiGraphState) -> Dict[str, Any]:
        task = state["task"]
        module = task.get("module_name") or "soc_block"
        files: Dict[str, str] = {}
        if state.get("reg_code"):
            files[f"{module}_reg_top.sv"] = state["reg_code"]
        if state.get("header_code"):
            files[f"{module}_regs.h"] = state["header_code"]
        if state.get("hal_code"):
            files[f"{module}_hal.c"] = state["hal_code"]
        if state.get("tb_code"):
            files[f"{module}_tb.sv"] = state["tb_code"]
        if state.get("driver_code"):
            files[f"{module}_driver.c"] = state["driver_code"]

        report_data: Dict[str, Any] = {
            "checks": state.get("checks", {}),
            "sim": state.get("sim_result", {}),
            "alignment": state.get("alignment_result", {}),
            "artifacts_summary": list(files.keys()),
        }
        artifacts = self.repository.persist_artifacts(
            output_dir=state.get("output_dir"),
            files=files,
            report_data=report_data,
            task=task,
            logs=state.get("logs", []),
            log_dir=self.settings.default_log_dir,
        )
        update: Dict[str, Any] = {}
        if artifacts:
            update["artifacts"] = artifacts
            update["logs"] = [append_log([], "persist_output", "completed", list(artifacts.keys()))]
        return update

    def _node_finalize(self, state: MultiGraphState) -> Dict[str, Any]:
        sim = state.get("sim_result", {})
        align = state.get("alignment_result", {})
        approval = state.get("approval", "approve")
        sim_ok = sim.get("passed") in ("passed", "skipped")
        align_ok = align.get("aligned", False) if align else True
        if approval != "approve":
            return {"status": TaskStatus.FAILED.value,
                    "error": f"approval:{approval}",
                    "checks": {"sim": sim.get("passed"), "alignment": align.get("aligned"),
                               "approval": approval}}
        if sim_ok and align_ok:
            return {"status": TaskStatus.COMPLETED.value,
                    "checks": {"sim": sim.get("passed"), "alignment": align.get("aligned")}}
        return {"status": TaskStatus.FAILED.value,
                "error": f"sim={sim.get('passed')} aligned={align.get('aligned')}",
                "checks": {"sim": sim.get("passed"), "alignment": align.get("aligned")}}

    # ------------------------------------------------------------------
    # Graph
    # ------------------------------------------------------------------
    def build_graph(self):
        graph: StateGraph = StateGraph(MultiGraphState)
        graph.add_node("parse_request", self._node_parse)
        graph.add_node("load_context", self._node_load_context)
        graph.add_edge(START, "parse_request")
        graph.add_edge("parse_request", "load_context")
        prev = "load_context"
        for step_name, task_type, upstream in _STEPS:
            graph.add_node(step_name, self._make_gen_node(step_name, task_type, upstream))
            graph.add_edge(prev, step_name)
            prev = step_name
        graph.add_node("run_simulation", self._node_run_sim)
        graph.add_edge(prev, "run_simulation")
        graph.add_node("align_check", self._node_align_check)
        graph.add_edge("run_simulation", "align_check")
        graph.add_node("approval_gate", self._node_approval_gate)
        graph.add_edge("align_check", "approval_gate")
        graph.add_node("persist_output", self._node_persist)
        # Conditional routing: only persist when approved; otherwise jump to
        # finalize so an unapproved artifact never lands on disk.
        graph.add_conditional_edges(
            "approval_gate",
            lambda state: "persist_output" if state.get("approval") == "approve" else "finalize",
            {"persist_output": "persist_output", "finalize": "finalize"},
        )
        graph.add_node("finalize", self._node_finalize)
        graph.add_edge("persist_output", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(
        self,
        request: str,
        context_path: Optional[str] = None,
        context_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        *,
        require_approval: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if require_approval is not None:
            self.require_approval = require_approval
        machine = TaskStateMachine()
        machine.transition(TaskStatus.RUNNING, detail=request)
        initial: Dict[str, Any] = {
            "request": request, "context_path": context_path,
            "context_dir": context_dir, "output_dir": output_dir,
            "context": "", "reg_code": "", "header_code": "", "hal_code": "",
            "tb_code": "", "driver_code": "", "sim_result": {},
            "alignment_result": {},
            "checks": {}, "logs": [], "status": TaskStatus.RUNNING.value,
            "artifacts": {}, "task": {}, "gate_failed": False, "approval": "",
        }
        final = self.build_graph().invoke(initial)
        machine.transition(
            TaskStatus.COMPLETED if final.get("status") == TaskStatus.COMPLETED.value else TaskStatus.FAILED,
            detail=final.get("status"),
        )
        return self._to_result(final)

    @staticmethod
    def _to_result(final: Dict[str, Any]) -> Dict[str, Any]:
        task = final.get("task", {})
        return {
            "status": final.get("status", TaskStatus.RUNNING.value),
            "task_type": task.get("task_type", "hw_sw_codesign"),
            "output": {
                "reg_code": final.get("reg_code", ""),
                "header_code": final.get("header_code", ""),
                "hal_code": final.get("hal_code", ""),
                "tb_code": final.get("tb_code", ""),
                "driver_code": final.get("driver_code", ""),
                "checks": final.get("checks", {}),
                "sim": final.get("sim_result", {}),
                "alignment": final.get("alignment_result", {}),
            },
            "logs": final.get("logs", []),
            "artifacts": final.get("artifacts", {}),
            **({"error": final["error"]} if final.get("error") else {}),
        }


def run_multistep(
    request: str,
    context_path: Optional[str] = None,
    context_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    *,
    require_approval: bool = False,
    approver: Optional[Approver] = None,
) -> Dict[str, Any]:
    """Run the multi-step co-design closed loop end-to-end.

    When ``require_approval`` is set, the gate consults ``approver`` (default:
    refuse) before persisting, so an unapproved artifact never lands on disk.
    """
    orch = MultiStepOrchestrator(require_approval=require_approval, approver=approver)
    return orch.run(
        request, context_path=context_path, context_dir=context_dir, output_dir=output_dir
    )
