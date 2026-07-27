"""Workflow orchestration layer (Section 3.4 / 6.1).

Builds the minimal LangGraph state machine that the phase 1 plan specifies:
parse -> execute -> validate -> persist -> return, with a fail branch for
unrecognised tasks. A :class:`TaskStateMachine` records transitions alongside
the graph so each run is inspectable and resumable.

The public entry point :func:`run_workflow` preserves the original phase 1
contract (return shape, ``output_dir``/``context_dir`` flags) so existing
tests and the ``chipagent`` CLI keep working.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .llm import LLMClient
from .logging_utils import append_log
from .models import TaskStatus, TaskObject
from .parser import TaskParser
from .repository import Repository
from .skills import SkillContext, SkillLoader, TextSkill
from .skills.base import Skill
from .state import TaskStateMachine
from .validation import VerilogLinter


class GraphState(TypedDict, total=False):
    """LangGraph state threaded through every node.

    ``logs`` uses an additive reducer so each node only appends its own entry.
    """

    request: str
    task: Dict[str, Any]
    context: str
    code: str
    design_notes: str
    checks: Dict[str, Any]
    logs: Annotated[List[Dict[str, Any]], operator.add]
    status: str
    artifacts: Dict[str, Any]
    output_dir: Optional[str]
    context_path: Optional[str]
    context_dir: Optional[str]
    error: Optional[str]
    code_path: Optional[str]


class WorkflowOrchestrator:
    """Assembles the LangGraph workflow and owns its collaborators."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        llm: Optional[LLMClient] = None,
        parser: Optional[TaskParser] = None,
        repository: Optional[Repository] = None,
        linter: Optional[VerilogLinter] = None,
        skills: Optional[List[Skill]] = None,
        state_dir: Optional[str] = None,
        use_llm: bool = True,
    ) -> None:
        self.settings = settings or Settings.load()
        self.llm = llm or LLMClient(self.settings.llm)
        self.parser = parser or TaskParser(self.llm, use_llm=use_llm)
        self.repository = repository or Repository(
            repo_root=self.settings.repo_root, use_git=self.settings.use_git
        )
        self.linter = linter or VerilogLinter()
        # Skills are discovered from text/<name>/SKILL.md directories. Each is
        # wrapped as a TextSkill with the LLM client for online generation.
        if skills is not None:
            self.skills: List[Skill] = skills
        else:
            loader = SkillLoader()
            self.skills = [
                TextSkill(loaded, llm=self.llm if use_llm else None)
                for loaded in loader.discover()
            ]
        self._state_dir = state_dir

    # ------------------------------------------------------------------
    # Node implementations
    # ------------------------------------------------------------------
    def _node_parse(self, state: GraphState) -> Dict[str, Any]:
        task = self.parser.parse(state["request"]).to_dict()
        return {
            "task": task,
            "status": TaskStatus.RUNNING.value,
            "logs": [append_log([], "parse_request", "completed", task)],
        }

    def _node_load_context(self, state: GraphState) -> Dict[str, Any]:
        context = self.repository.load_context(
            context_path=state.get("context_path"),
            context_dir=state.get("context_dir"),
        )
        update: Dict[str, Any] = {"context": context}
        if context:
            entry = {"step": "load_context", "status": "completed", "detail": context[:200]}
            update["logs"] = [entry]
        return update

    def _node_generate(self, state: GraphState) -> Dict[str, Any]:
        task_dict = state["task"]
        task = TaskObject(
            task_type=task_dict.get("task_type", "rtl_generation"),
            module_name=task_dict.get("module_name"),
            description=task_dict.get("description", ""),
            interface=task_dict.get("interface", {}) or {},
            constraints=task_dict.get("constraints", {}) or {},
            output_requirements=task_dict.get("output_requirements", {}) or {},
        )
        ctx = SkillContext(task=task, context=state.get("context", ""), llm=self.llm)
        result = self._select_skill(task.task_type).run(ctx)
        return {
            "code": result.code,
            "design_notes": result.design_notes,
            "logs": [append_log([], "generate_rtl", "completed", "rtl code generated")],
        }

    def _node_validate(self, state: GraphState) -> Dict[str, Any]:
        checks = self.linter.run(state["code"], code_path=state.get("code_path"))
        return {
            "checks": checks,
            "logs": [append_log([], "validate", "completed", checks)],
        }

    def _node_persist(self, state: GraphState) -> Dict[str, Any]:
        artifacts = self.repository.persist(
            output_dir=state.get("output_dir"),
            task=state["task"],
            code=state["code"],
            design_notes=state.get("design_notes", ""),
            checks=state["checks"],
            logs=state.get("logs", []),
            log_dir=self.settings.default_log_dir,
        )
        update: Dict[str, Any] = {}
        if artifacts:
            update["artifacts"] = artifacts
            update["code_path"] = artifacts.get("code_path")
            update["logs"] = [append_log([], "persist_output", "completed", artifacts)]
        return update

    def _node_finalize(self, state: GraphState) -> Dict[str, Any]:
        """Mark the run completed (or failed if validation failed)."""
        checks = state.get("checks", {})
        if checks.get("lint") == "passed":
            return {"status": TaskStatus.COMPLETED.value}
        return {"status": TaskStatus.FAILED.value, "error": "validation failed"}

    def _node_fail(self, state: GraphState) -> Dict[str, Any]:
        msg = f"unsupported task_type: {state['task'].get('task_type')}"
        return {
            "status": TaskStatus.FAILED.value,
            "error": msg,
            "checks": {"lint": "failed", "syntax": "skipped"},
            "logs": [append_log([], "validate", "failed", msg)],
        }

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def _route_after_parse(self, state: GraphState) -> str:
        """Route to the generate node if a Skill handles this task_type."""
        task_type = state["task"].get("task_type")
        return "generate_rtl" if any(s.name == task_type for s in self.skills) else "fail"

    def _select_skill(self, task_type: str) -> Skill:
        for skill in self.skills:
            if skill.name == task_type:
                return skill
        return self.skills[0]

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------
    def build_graph(self):
        graph = StateGraph(GraphState)
        graph.add_node("parse_request", self._node_parse)
        graph.add_node("load_context", self._node_load_context)
        graph.add_node("generate_rtl", self._node_generate)
        graph.add_node("validate", self._node_validate)
        graph.add_node("persist_output", self._node_persist)
        graph.add_node("finalize", self._node_finalize)
        graph.add_node("fail", self._node_fail)

        graph.add_edge(START, "parse_request")
        graph.add_edge("parse_request", "load_context")
        graph.add_conditional_edges(
            "load_context",
            self._route_after_parse,
            {"generate_rtl": "generate_rtl", "fail": "fail"},
        )
        graph.add_edge("generate_rtl", "validate")
        graph.add_edge("validate", "persist_output")
        graph.add_edge("persist_output", "finalize")
        graph.add_edge("finalize", END)
        graph.add_edge("fail", END)
        return graph.compile()

    # ------------------------------------------------------------------
    # Public run API
    # ------------------------------------------------------------------
    def run(
        self,
        request: str,
        context_path: Optional[str] = None,
        context_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        machine = TaskStateMachine(state_dir=self._state_dir)
        machine.transition(TaskStatus.RUNNING, detail=request)

        initial: Dict[str, Any] = {
            "request": request,
            "context_path": context_path,
            "context_dir": context_dir,
            "output_dir": output_dir,
            "context": "",
            "code": "",
            "design_notes": "",
            "checks": {},
            "logs": [],
            "status": TaskStatus.RUNNING.value,
            "artifacts": {},
            "error": None,
            "code_path": None,
            "task": {},
        }
        app = self.build_graph()
        final = app.invoke(initial)

        final_status = final.get("status", TaskStatus.RUNNING.value)
        machine.transition(
            TaskStatus.COMPLETED
            if final_status == TaskStatus.COMPLETED.value or final.get("code")
            else TaskStatus.FAILED,
            detail=final_status,
        )

        return self._to_result(final)

    # ------------------------------------------------------------------
    # Result shaping (matches the legacy phase 1 contract)
    # ------------------------------------------------------------------
    @staticmethod
    def _to_result(final: Dict[str, Any]) -> Dict[str, Any]:
        task = final.get("task", {})
        checks = final.get("checks", {})
        normalized_checks = {
            "lint": checks.get("lint", "failed"),
            "syntax": checks.get("syntax", "skipped"),
        }
        for k, v in checks.items():
            if k not in normalized_checks:
                normalized_checks[k] = v

        result = {
            "status": final.get("status", TaskStatus.RUNNING.value),
            "task_type": task.get("task_type", "unknown"),
            "output": {
                "code": final.get("code", ""),
                "checks": normalized_checks,
                "design_notes": final.get("design_notes", ""),
            },
            "logs": final.get("logs", []),
            "artifacts": final.get("artifacts", {}),
        }
        if final.get("error"):
            result["error"] = final["error"]
        return result


# ----------------------------------------------------------------------
# Backward-compatible functional API (used by tests + the CLI entry point)
# ----------------------------------------------------------------------
def run_workflow(
    request: str,
    context_path: Optional[str] = None,
    context_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a ChipAgent workflow end-to-end and return the structured result.

    Dispatch rules:
    - ``hw_sw_codesign`` task type (or ``CHIPAGENT_MULTISTEP=1``) runs the
      multi-step co-design closed loop (reg→header→tb→sim→driver→align).
    - ``CHIPAGENT_USE_TEMPORAL=1`` dispatches to a real Temporal workflow
      (single-step ``ChipAgentWorkflow`` or multi-step ``MultiStepWorkflow``).
    - Otherwise the in-process LangGraph single-skill orchestrator runs.
    """
    import os

    from .parser import TaskParser

    use_llm = os.environ.get("CHIPAGENT_DISABLE_LLM", "0") != "1"
    # Cheap rule-based parse to pick the path; the orchestrator re-parses
    # (optionally with LLM) inside, so this is only for dispatch.
    task_type = TaskParser(llm=None, use_llm=False).parse(request).task_type
    is_multistep = task_type == "hw_sw_codesign" or os.environ.get("CHIPAGENT_MULTISTEP", "0") == "1"

    if os.environ.get("CHIPAGENT_USE_TEMPORAL", "0") == "1":
        if is_multistep:
            from .temporal_runtime import run_multistep_via_temporal
            return run_multistep_via_temporal(
                request, context_path=context_path, context_dir=context_dir,
                output_dir=output_dir, use_llm=use_llm,
            )
        from .temporal_runtime import run_via_temporal
        return run_via_temporal(
            request, context_path=context_path, context_dir=context_dir,
            output_dir=output_dir, use_llm=use_llm,
        )

    if is_multistep:
        from .multistep import run_multistep
        return run_multistep(
            request, context_path=context_path, context_dir=context_dir, output_dir=output_dir,
        )
    return WorkflowOrchestrator(use_llm=use_llm).run(
        request, context_path=context_path, context_dir=context_dir, output_dir=output_dir,
    )


def main() -> None:
    """CLI entry point.

    Two forms:
      ``chipagent "<request>" [--context ...] [--output-dir ...]``
      ``chipagent task <submit|list|status|pause|resume|approve|rollback> ...``

    The ``task`` subcommand dispatches to the Phase 2 task panel
    (:mod:`chipagent.taskpanel`); ``dse`` dispatches to the DSE runner; everything
    else is the phase-1 single request. Interactive chat is intentionally not
    routed here; ChipAgent's interactive surface is the MCP host.
    """
    import argparse
    import json
    import sys
    from pathlib import Path

    if len(sys.argv) > 1 and sys.argv[1] == "task":
        # Hand the rest of the args to the task panel CLI.
        from .taskpanel import main as task_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        task_main()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "dse":
        # Phase 2 DSE agent loop: design-space exploration with backend feedback.
        from .dse.runner import main as dse_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        dse_main()
        return

    parser = argparse.ArgumentParser(description="Run the ChipAgent prototype")
    parser.add_argument("request", help="Natural language request to process")
    parser.add_argument("--context", dest="context_path", help="Path to a repository context file")
    parser.add_argument(
        "--context-dir", dest="context_dir", help="Directory to read repository context files from"
    )
    parser.add_argument("--output-dir", dest="output_dir", help="Directory for generated artifacts")
    parser.add_argument(
        "--no-llm",
        dest="no_llm",
        action="store_true",
        help="Disable LLM calls and use the deterministic template fallback",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or str(Path("generated").resolve())
    orchestrator = WorkflowOrchestrator(use_llm=not args.no_llm)
    result = orchestrator.run(
        args.request,
        context_path=args.context_path,
        context_dir=args.context_dir,
        output_dir=output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
