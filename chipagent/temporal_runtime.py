"""Temporal workflow integration (Section 3.4 / 10.6).

A REAL Temporal workflow + worker that mirrors the LangGraph node chain
(parse -> load_context -> generate -> validate -> persist). Enabled when
``CHIPAGENT_USE_TEMPORAL=1``; the worker connects to a Temporal server
(default ``localhost:7233``, the dev server started via ``temporal server
start-dev``).

This is the genuine Temporal integration the phase-1 plan calls out — not the
in-process ``TaskStateMachine`` stand-in. The stand-in remains the default
offline state backend; Temporal is the production-grade path with durable
state, retry, and recovery handled by the Temporal server.

Run the worker (registers workflow + activities, polls the task queue):

    python -m chipagent.temporal_runtime worker

Submit a workflow and wait for the result:

    python -m chipagent.temporal_runtime submit "请为 AXI DMA 模块生成 RTL" --output-dir ./generated

Or programmatically:

    from chipagent.temporal_runtime import run_via_temporal
    result = run_via_temporal("请为 AXI DMA 模块生成 RTL", output_dir="./generated")
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from .config import Settings
from .llm import LLMClient
from .logging_utils import append_log
from .models import TaskObject, TaskStatus
from .parser import TaskParser
from .repository import Repository
from .skills import SkillContext, SkillLoader, TextSkill
from .skills.base import Skill
from .validation import VerilogLinter

# Task queue shared by worker and client.
TASK_QUEUE = "chipagent-phase1"


# ----------------------------------------------------------------------
# Activities — thin wrappers over the same collaborators the LangGraph
# orchestrator uses. Activities must be deterministic-ish and serialisable;
# they return plain dicts.
# ----------------------------------------------------------------------
@dataclass
class ActivityContext:
    """Holds collaborators for activities. Built once when the worker starts."""

    settings: Settings
    llm: LLMClient
    parser: TaskParser
    repository: Repository
    linter: VerilogLinter
    skills: List[Skill] = field(default_factory=list)
    tools: list = field(default_factory=list)  # Phase 2: Tool instances (sim, align)


def build_activity_context(use_llm: bool = True) -> ActivityContext:
    from .tools import ToolLoader

    settings = Settings.load()
    llm = LLMClient(settings.llm)
    loader = SkillLoader()
    skills = [TextSkill(loaded, llm=llm if use_llm else None) for loaded in loader.discover()]
    tools = ToolLoader().discover()
    return ActivityContext(
        settings=settings,
        llm=llm,
        parser=TaskParser(llm, use_llm=use_llm),
        repository=Repository(repo_root=settings.repo_root, use_git=settings.use_git),
        linter=VerilogLinter(),
        skills=skills,
        tools=tools,
    )


_act_ctx: Optional[ActivityContext] = None


def _ctx() -> ActivityContext:
    global _act_ctx
    if _act_ctx is None:
        _act_ctx = build_activity_context()
    return _act_ctx


def set_activity_context(ctx: ActivityContext) -> None:
    global _act_ctx
    _act_ctx = ctx


@activity.defn
async def act_parse(request: str) -> Dict[str, Any]:
    task = _ctx().parser.parse(request).to_dict()
    return task


@activity.defn
async def act_load_context(args: Dict[str, Any]) -> str:
    context = _ctx().repository.load_context(
        context_path=args.get("context_path"),
        context_dir=args.get("context_dir"),
    )
    return context


@activity.defn
async def act_generate(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Skill that matches task.task_type."""
    task_dict = args["task"]
    task = TaskObject(
        task_type=task_dict.get("task_type", "rtl_generation"),
        module_name=task_dict.get("module_name"),
        description=task_dict.get("description", ""),
        interface=task_dict.get("interface", {}) or {},
        constraints=task_dict.get("constraints", {}) or {},
        output_requirements=task_dict.get("output_requirements", {}) or {},
    )
    skill = next(
        (s for s in _ctx().skills if s.name == task.task_type),
        None,
    )
    if skill is None:
        return {"code": "", "design_notes": f"no skill for task_type={task.task_type}", "ok": False}
    result = skill.run(SkillContext(task=task, context=args.get("context", ""), llm=_ctx().llm))
    return {"code": result.code, "design_notes": result.design_notes, "ok": bool(result.code)}


@activity.defn
async def act_validate(code: str) -> Dict[str, Any]:
    return _ctx().linter.run(code)


@activity.defn
async def act_persist(args: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = _ctx().repository.persist(
        output_dir=args.get("output_dir"),
        task=args["task"],
        code=args["code"],
        design_notes=args.get("design_notes", ""),
        checks=args["checks"],
        logs=args.get("logs", []),
        log_dir=_ctx().settings.default_log_dir,
    )
    return artifacts


# ----------------------------------------------------------------------
# Multi-step activities (Phase 2 co-design closed loop)
# ----------------------------------------------------------------------
@activity.defn
async def act_generate_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a named Skill with upstream artifact as context (multi-step)."""
    task_dict = args["task"]
    constraints = dict(task_dict.get("constraints") or {})
    constraints.setdefault("clock", True)
    constraints.setdefault("reset", True)
    constraints.setdefault("data_width", 32)
    task = TaskObject(
        task_type=args["skill_type"],
        module_name=task_dict.get("module_name"),
        description=task_dict.get("description", ""),
        interface=task_dict.get("interface", {}) or {},
        constraints=constraints,
        output_requirements=task_dict.get("output_requirements", {}) or {},
    )
    skill = next((s for s in _ctx().skills if s.name == args["skill_type"]), None)
    if skill is None:
        return {"code": "", "ok": False, "reason": f"no skill for {args['skill_type']}"}
    result = skill.run(SkillContext(task=task, context=args.get("context", ""), llm=_ctx().llm))
    return {"code": result.code, "design_notes": result.design_notes, "ok": bool(result.code)}


@activity.defn
async def act_run_simulation(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compile + run the RTL reg block against the testbench."""
    from .tools import ToolContext

    tool = next((t for t in _ctx().tools if t.name == "run_simulation"), None)
    if tool is None:
        return {"passed": "skipped", "reason": "no sim tool"}
    res = tool.run(ToolContext(
        task=TaskObject(task_type="hw_sw_codesign", module_name=args.get("module_name", "soc_block"),
                        description="", constraints={"data_width": args.get("data_width", 32)}),
        inputs={"reg_code": args.get("reg_code", ""), "tb_code": args.get("tb_code", "")},
        settings=_ctx().settings,
    ))
    return res.result


@activity.defn
async def act_check_alignment(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check RTL reg block offsets vs C header defines."""
    from .tools import ToolContext

    tool = next((t for t in _ctx().tools if t.name == "check_register_alignment"), None)
    if tool is None:
        return {"aligned": False, "reason": "no align tool"}
    res = tool.run(ToolContext(
        task=TaskObject(task_type="hw_sw_codesign", module_name=args.get("module_name", "soc_block"),
                        description="", constraints={}),
        inputs={"reg_code": args.get("reg_code", ""), "header_code": args.get("header_code", "")},
        settings=_ctx().settings,
    ))
    return res.result


# ----------------------------------------------------------------------
# Workflow — the durable orchestration. Mirrors the LangGraph chain but the
# Temporal server owns state/retry/recovery.
# ----------------------------------------------------------------------
@workflow.defn
class ChipAgentWorkflow:
    def __init__(self) -> None:
        self.logs: List[Dict[str, Any]] = []

    @workflow.run
    async def run(
        self,
        request: str,
        context_path: Optional[str] = None,
        context_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        task = await workflow.execute_activity(
            act_parse, request, start_to_close_timeout=timeout(30)
        )
        self.logs.append(append_log([], "parse_request", "completed", task))

        context = await workflow.execute_activity(
            act_load_context,
            {"context_path": context_path, "context_dir": context_dir},
            start_to_close_timeout=timeout(30),
        )
        if context:
            self.logs.append({"step": "load_context", "status": "completed", "detail": context[:200]})

        # Route: need a Skill that handles this task_type.
        task_type = task.get("task_type")
        # The workflow cannot see the worker's in-memory skill list directly,
        # so it asks the generate activity, which returns ok=False if no skill.
        gen = await workflow.execute_activity(
            act_generate,
            {"task": task, "context": context},
            start_to_close_timeout=timeout(120),
        )
        if not gen.get("ok"):
            msg = gen.get("design_notes") or f"unsupported task_type: {task_type}"
            self.logs.append(append_log([], "validate", "failed", msg))
            checks = {"lint": "failed", "syntax": "skipped"}
            await workflow.execute_activity(
                act_persist,
                {
                    "output_dir": output_dir,
                    "task": task,
                    "code": "",
                    "design_notes": msg,
                    "checks": checks,
                    "logs": self.logs,
                },
                start_to_close_timeout=timeout(30),
            )
            return {
                "status": TaskStatus.FAILED.value,
                "task_type": task_type,
                "output": {"code": "", "checks": checks, "design_notes": msg},
                "logs": self.logs,
                "artifacts": {},
                "error": msg,
            }

        self.logs.append(append_log([], "generate_rtl", "completed", "rtl code generated"))
        code = gen["code"]
        design_notes = gen.get("design_notes", "")

        checks = await workflow.execute_activity(
            act_validate, code, start_to_close_timeout=timeout(60)
        )
        self.logs.append(append_log([], "validate", "completed", checks))

        artifacts = await workflow.execute_activity(
            act_persist,
            {
                "output_dir": output_dir,
                "task": task,
                "code": code,
                "design_notes": design_notes,
                "checks": checks,
                "logs": self.logs,
            },
            start_to_close_timeout=timeout(30),
        )
        if artifacts:
            self.logs.append(append_log([], "persist_output", "completed", artifacts))

        status = TaskStatus.COMPLETED.value if checks.get("lint") == "passed" else TaskStatus.FAILED.value
        normalized = {
            "lint": checks.get("lint", "failed"),
            "syntax": checks.get("syntax", "skipped"),
        }
        for k, v in checks.items():
            if k not in normalized:
                normalized[k] = v
        return {
            "status": status,
            "task_type": task_type,
            "output": {"code": code, "checks": normalized, "design_notes": design_notes},
            "logs": self.logs,
            "artifacts": artifacts,
        }


def timeout(seconds: int):
    """timedelta helper (Temporal accepts timedelta)."""
    from datetime import timedelta

    return timedelta(seconds=seconds)


# Phase 2 §4.2 / §6 Step 2: a conservative activity-level retry policy.
# Transient failures (subprocess timeout, LLM gateway hiccup) get retried up
# to ``maximum_attempts``; the backoff is Temporal's default (exponential).
def retry_policy(maximum_attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(maximum_attempts=maximum_attempts, non_retryable_error_types=[])


# ----------------------------------------------------------------------
# Multi-step co-design workflow (Phase 2 Step 3). Mirrors the LangGraph
# MultiStepOrchestrator chain but the Temporal server owns durable state.
# ----------------------------------------------------------------------
@workflow.defn
class MultiStepWorkflow:
    def __init__(self) -> None:
        self.logs: List[Dict[str, Any]] = []

    @workflow.run
    async def run(
        self,
        request: str,
        context_path: Optional[str] = None,
        context_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        task = await workflow.execute_activity(
            act_parse, request, start_to_close_timeout=timeout(30)
        )
        self.logs.append(append_log([], "parse_request", "completed", task))

        context = await workflow.execute_activity(
            act_load_context,
            {"context_path": context_path, "context_dir": context_dir},
            start_to_close_timeout=timeout(30),
        )
        if context:
            self.logs.append({"step": "load_context", "status": "completed", "detail": context[:200]})

        module = task.get("module_name") or "soc_block"
        steps = [
            ("reg_definition", None, "reg_code"),
            ("register_header", "reg_code", "header_code"),
            ("hal_library", "header_code", "hal_code"),
            ("testbench_generation", "reg_code", "tb_code"),
            ("linux_driver", "header_code", "driver_code"),
        ]
        artifacts: Dict[str, str] = {}
        for skill_type, upstream, out_field in steps:
            gen = await workflow.execute_activity(
                act_generate_skill,
                {"task": task, "skill_type": skill_type,
                 "context": artifacts.get(upstream, "") if upstream else ""},
                start_to_close_timeout=timeout(120),
            )
            if not gen.get("ok"):
                self.logs.append(append_log([], skill_type, "failed", gen.get("reason", "")))
                continue
            artifacts[out_field] = gen["code"]
            self.logs.append(append_log([], skill_type, "completed", f"{len(gen['code'])} chars"))

        sim = await workflow.execute_activity(
            act_run_simulation,
            {"module_name": module, "reg_code": artifacts.get("reg_code", ""),
             "tb_code": artifacts.get("tb_code", "")},
            start_to_close_timeout=timeout(120),
        )
        self.logs.append(append_log([], "run_simulation", "completed", sim))

        align = await workflow.execute_activity(
            act_check_alignment,
            {"module_name": module, "reg_code": artifacts.get("reg_code", ""),
             "header_code": artifacts.get("header_code", "")},
            start_to_close_timeout=timeout(30),
        )
        self.logs.append(append_log([], "align_check", "completed", align))

        files: Dict[str, str] = {}
        if artifacts.get("reg_code"):
            files[f"{module}_reg_top.sv"] = artifacts["reg_code"]
        if artifacts.get("header_code"):
            files[f"{module}_regs.h"] = artifacts["header_code"]
        if artifacts.get("hal_code"):
            files[f"{module}_hal.c"] = artifacts["hal_code"]
        if artifacts.get("tb_code"):
            files[f"{module}_tb.sv"] = artifacts["tb_code"]
        if artifacts.get("driver_code"):
            files[f"{module}_driver.c"] = artifacts["driver_code"]
        report_data = {"checks": {}, "sim": sim, "alignment": align,
                       "artifacts_summary": list(files.keys())}
        from .repository import Repository as _Repo
        repo = _Repo(repo_root=_ctx().settings.repo_root, use_git=_ctx().settings.use_git)
        persisted = repo.persist_artifacts(
            output_dir=output_dir, files=files, report_data=report_data,
            task=task, logs=self.logs, log_dir=_ctx().settings.default_log_dir,
        )
        if persisted:
            self.logs.append(append_log([], "persist_output", "completed", list(persisted.keys())))

        sim_ok = sim.get("passed") in ("passed", "skipped")
        align_ok = bool(align.get("aligned"))
        status = TaskStatus.COMPLETED.value if (sim_ok and align_ok) else TaskStatus.FAILED.value
        return {
            "status": status,
            "task_type": task.get("task_type", "hw_sw_codesign"),
            "output": {
                "reg_code": artifacts.get("reg_code", ""),
                "header_code": artifacts.get("header_code", ""),
                "hal_code": artifacts.get("hal_code", ""),
                "tb_code": artifacts.get("tb_code", ""),
                "driver_code": artifacts.get("driver_code", ""),
                "checks": {"sim": sim.get("passed"), "alignment": align.get("aligned")},
                "sim": sim, "alignment": align,
            },
            "logs": self.logs,
            "artifacts": persisted,
        }


# ----------------------------------------------------------------------
# Reliable multi-step workflow (Phase 2 Step 2). Adds activity RetryPolicy,
# pause/resume/approval/cancel signals, and a status query on top of the
# MultiStepWorkflow chain. The approval gate is opt-in (``require_approval``)
# so the default e2e path still completes without sending a signal.
# ----------------------------------------------------------------------
@workflow.defn
class ReliableMultiStepWorkflow:
    """Multi-step co-design workflow with human-in-the-loop controls."""

    def __init__(self) -> None:
        self.logs: List[Dict[str, Any]] = []
        self.current_step = "init"
        self.progress: int = 0
        self.artifacts_so_far: Dict[str, str] = {}
        self.paused = False
        self.cancelled = False
        self.pending_approval = False
        self._approval: Optional[str] = None  # "approve" | "reject" | "modify"

    # -- signals ------------------------------------------------------
    @workflow.signal
    def pause(self) -> None:
        self.paused = True

    @workflow.signal
    def resume(self) -> None:
        self.paused = False

    @workflow.signal
    def cancel(self) -> None:
        self.cancelled = True

    @workflow.signal
    def approval(self, decision: str) -> None:
        self._approval = decision
        self.pending_approval = False

    # -- query --------------------------------------------------------
    @workflow.query
    def status(self) -> Dict[str, Any]:
        return {
            "current_step": self.current_step,
            "progress": self.progress,
            "artifacts_so_far": list(self.artifacts_so_far.keys()),
            "paused": self.paused,
            "pending_approval": self.pending_approval,
            "cancelled": self.cancelled,
        }

    # -- helpers ------------------------------------------------------
    async def _gate_pause(self) -> None:
        """Block while a pause signal has been received."""
        await workflow.wait_condition(lambda: not self.paused or self.cancelled)

    async def _await_approval(self, step: str) -> str:
        """Block at an approval gate until a decision signal arrives."""
        self.pending_approval = True
        self.current_step = f"{step}:awaiting_approval"
        self.logs.append(append_log([], step, "awaiting_approval", None))
        await workflow.wait_condition(lambda: self._approval is not None or self.cancelled)
        self.pending_approval = False
        decision = self._approval or ("cancel" if self.cancelled else "reject")
        self._approval = None
        return decision

    # -- run ----------------------------------------------------------
    @workflow.run
    async def run(
        self,
        request: str,
        context_path: Optional[str] = None,
        context_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
        require_approval: bool = False,
    ) -> Dict[str, Any]:
        rp = retry_policy(maximum_attempts=3)
        task = await workflow.execute_activity(
            act_parse, request,
            start_to_close_timeout=timeout(30), retry_policy=rp,
        )
        self.current_step = "parse_request"
        self.progress = 10
        self.logs.append(append_log([], "parse_request", "completed", task))

        await self._gate_pause()
        if self.cancelled:
            return self._cancelled_result(task)

        context = await workflow.execute_activity(
            act_load_context,
            {"context_path": context_path, "context_dir": context_dir},
            start_to_close_timeout=timeout(30), retry_policy=rp,
        )

        module = task.get("module_name") or "soc_block"
        steps = [
            ("reg_definition", None, "reg_code"),
            ("register_header", "reg_code", "header_code"),
            ("hal_library", "header_code", "hal_code"),
            ("testbench_generation", "reg_code", "tb_code"),
            ("linux_driver", "header_code", "driver_code"),
        ]
        for idx, (skill_type, upstream, out_field) in enumerate(steps):
            self.current_step = skill_type
            self.progress = 20 + idx * 10
            await self._gate_pause()
            if self.cancelled:
                return self._cancelled_result(task)
            gen = await workflow.execute_activity(
                act_generate_skill,
                {"task": task, "skill_type": skill_type,
                 "context": self.artifacts_so_far.get(upstream, "") if upstream else ""},
                start_to_close_timeout=timeout(120), retry_policy=rp,
            )
            if not gen.get("ok"):
                self.logs.append(append_log([], skill_type, "failed", gen.get("reason", "")))
                continue
            self.artifacts_so_far[out_field] = gen["code"]
            self.logs.append(append_log([], skill_type, "completed", f"{len(gen['code'])} chars"))

        # Verify gate (§4.4): run simulation.
        self.current_step = "run_simulation"
        self.progress = 70
        await self._gate_pause()
        if self.cancelled:
            return self._cancelled_result(task)
        sim = await workflow.execute_activity(
            act_run_simulation,
            {"module_name": module, "reg_code": self.artifacts_so_far.get("reg_code", ""),
             "tb_code": self.artifacts_so_far.get("tb_code", "")},
            start_to_close_timeout=timeout(120), retry_policy=rp,
        )
        self.logs.append(append_log([], "run_simulation", "completed", sim))

        # Align gate — escalate to a human when configured.
        self.current_step = "align_check"
        self.progress = 80
        align = await workflow.execute_activity(
            act_check_alignment,
            {"module_name": module, "reg_code": self.artifacts_so_far.get("reg_code", ""),
             "header_code": self.artifacts_so_far.get("header_code", "")},
            start_to_close_timeout=timeout(30), retry_policy=rp,
        )
        self.logs.append(append_log([], "align_check", "completed", align))

        if require_approval and not align.get("aligned"):
            decision = await self._await_approval("align_check")
            self.logs.append(append_log([], "align_check", "approval", decision))
            if decision != "approve":
                return self._cancelled_result(task, reason=f"approval:{decision}")

        # Persist.
        self.current_step = "persist_output"
        self.progress = 90
        files: Dict[str, str] = {}
        if self.artifacts_so_far.get("reg_code"):
            files[f"{module}_reg_top.sv"] = self.artifacts_so_far["reg_code"]
        if self.artifacts_so_far.get("header_code"):
            files[f"{module}_regs.h"] = self.artifacts_so_far["header_code"]
        if self.artifacts_so_far.get("hal_code"):
            files[f"{module}_hal.c"] = self.artifacts_so_far["hal_code"]
        if self.artifacts_so_far.get("tb_code"):
            files[f"{module}_tb.sv"] = self.artifacts_so_far["tb_code"]
        if self.artifacts_so_far.get("driver_code"):
            files[f"{module}_driver.c"] = self.artifacts_so_far["driver_code"]
        report_data = {"checks": {}, "sim": sim, "alignment": align,
                       "artifacts_summary": list(files.keys())}
        from .repository import Repository as _Repo
        repo = _Repo(repo_root=_ctx().settings.repo_root, use_git=_ctx().settings.use_git)
        persisted = repo.persist_artifacts(
            output_dir=output_dir, files=files, report_data=report_data,
            task=task, logs=self.logs, log_dir=_ctx().settings.default_log_dir,
        )
        if persisted:
            self.logs.append(append_log([], "persist_output", "completed", list(persisted.keys())))

        sim_ok = sim.get("passed") in ("passed", "skipped")
        align_ok = bool(align.get("aligned"))
        status = TaskStatus.COMPLETED.value if (sim_ok and align_ok) else TaskStatus.FAILED.value
        self.current_step = "done"
        self.progress = 100
        return {
            "status": status,
            "task_type": task.get("task_type", "hw_sw_codesign"),
            "output": {
                "reg_code": self.artifacts_so_far.get("reg_code", ""),
                "header_code": self.artifacts_so_far.get("header_code", ""),
                "hal_code": self.artifacts_so_far.get("hal_code", ""),
                "tb_code": self.artifacts_so_far.get("tb_code", ""),
                "driver_code": self.artifacts_so_far.get("driver_code", ""),
                "checks": {"sim": sim.get("passed"), "alignment": align.get("aligned")},
                "sim": sim, "alignment": align,
            },
            "logs": self.logs,
            "artifacts": persisted,
        }

    def _cancelled_result(self, task: Dict[str, Any], reason: str = "cancelled") -> Dict[str, Any]:
        return {
            "status": TaskStatus.FAILED.value,
            "task_type": task.get("task_type", "hw_sw_codesign"),
            "output": {"checks": {}, "reason": reason,
                       "artifacts_so_far": list(self.artifacts_so_far.keys())},
            "logs": self.logs,
            "artifacts": {},
            "error": reason,
        }


# ----------------------------------------------------------------------
# Client: submit a workflow and await its result.
# ----------------------------------------------------------------------
async def _submit_and_wait(
    client: Client,
    request: str,
    context_path: Optional[str],
    context_dir: Optional[str],
    output_dir: Optional[str],
) -> Dict[str, Any]:
    handle = await client.start_workflow(
        ChipAgentWorkflow.run,
        args=[request, context_path, context_dir, output_dir],
        id=f"chipagent-{uuid.uuid4().hex[:12]}",
        task_queue=TASK_QUEUE,
    )
    return await handle.result()


def run_via_temporal(
    request: str,
    context_path: Optional[str] = None,
    context_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    *,
    address: Optional[str] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Submit the single-step ChipAgent workflow to a Temporal server."""
    target = address or os.environ.get("CHIPAGENT_TEMPORAL_TARGET", "localhost:7233")
    set_activity_context(build_activity_context(use_llm=use_llm))
    return asyncio.run(
        _run_client(target, request, context_path, context_dir, output_dir)
    )


def run_multistep_via_temporal(
    request: str,
    context_path: Optional[str] = None,
    context_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    *,
    address: Optional[str] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Submit the multi-step co-design workflow to a Temporal server."""
    target = address or os.environ.get("CHIPAGENT_TEMPORAL_TARGET", "localhost:7233")
    set_activity_context(build_activity_context(use_llm=use_llm))
    return asyncio.run(
        _run_multistep_client(target, request, context_path, context_dir, output_dir)
    )


async def _run_client(target, request, context_path, context_dir, output_dir) -> Dict[str, Any]:
    client = await Client.connect(target)
    return await _submit_and_wait(client, request, context_path, context_dir, output_dir)


async def _run_multistep_client(target, request, context_path, context_dir, output_dir) -> Dict[str, Any]:
    client = await Client.connect(target)
    handle = await client.start_workflow(
        MultiStepWorkflow.run,
        args=[request, context_path, context_dir, output_dir],
        id=f"chipagent-ms-{uuid.uuid4().hex[:12]}",
        task_queue=TASK_QUEUE,
    )
    return await handle.result()


async def _run_reliable_client(
    target, request, context_path, context_dir, output_dir, require_approval
) -> Dict[str, Any]:
    client = await Client.connect(target)
    handle = await client.start_workflow(
        ReliableMultiStepWorkflow.run,
        args=[request, context_path, context_dir, output_dir, require_approval],
        id=f"chipagent-rel-{uuid.uuid4().hex[:12]}",
        task_queue=TASK_QUEUE,
    )
    return await handle.result()


def run_reliable_multistep_via_temporal(
    request: str,
    context_path: Optional[str] = None,
    context_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    *,
    require_approval: bool = False,
    address: Optional[str] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Submit the reliable multi-step workflow (retry + signals + query)."""
    target = address or os.environ.get("CHIPAGENT_TEMPORAL_TARGET", "localhost:7233")
    set_activity_context(build_activity_context(use_llm=use_llm))
    return asyncio.run(
        _run_reliable_client(target, request, context_path, context_dir, output_dir, require_approval)
    )


# ----------------------------------------------------------------------
# Worker: register workflow + activities and poll the task queue.
# ----------------------------------------------------------------------
async def _run_worker(target: str) -> None:
    client = await Client.connect(target)
    set_activity_context(build_activity_context())
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ChipAgentWorkflow, MultiStepWorkflow, ReliableMultiStepWorkflow],
        activities=[
            act_parse, act_load_context, act_generate, act_validate, act_persist,
            act_generate_skill, act_run_simulation, act_check_alignment,
        ],
        # The sandbox re-imports the workflow module without its package
        # context, breaking the relative imports used elsewhere in the
        # chipagent package. Use the unsandboxed runner.
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    print(f"Temporal worker listening on {target}, task_queue={TASK_QUEUE}", flush=True)
    await worker.run()


def main() -> None:
    """CLI entry: ``worker`` or ``submit "<request>"``."""
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="ChipAgent Temporal runtime")
    sub = parser.add_subparsers(dest="cmd", required=True)
    wp = sub.add_parser("worker", help="Run the Temporal worker")
    wp.add_argument("--address", default=None)
    s = sub.add_parser("submit", help="Submit a workflow and print the result")
    s.add_argument("request")
    s.add_argument("--context", dest="context_path")
    s.add_argument("--context-dir", dest="context_dir")
    s.add_argument("--output-dir", dest="output_dir")
    s.add_argument("--no-llm", dest="no_llm", action="store_true")
    s.add_argument("--address", default=None)
    args = parser.parse_args()

    target = args.address or os.environ.get("CHIPAGENT_TEMPORAL_TARGET", "localhost:7233")

    if args.cmd == "worker":
        asyncio.run(_run_worker(target))
        return

    output_dir = args.output_dir or str(Path("generated").resolve())
    result = run_via_temporal(
        args.request,
        context_path=args.context_path,
        context_dir=args.context_dir,
        output_dir=output_dir,
        address=target,
        use_llm=not args.no_llm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
