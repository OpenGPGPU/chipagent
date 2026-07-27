"""Task panel CLI + human approval (Phase 2 Step 7 / §3.1 §4.6).

A thin coordinator over the multi-step orchestrator, the state machine, and
the audit log. Exposes the task-panel command family the design spec §3.1
calls out::

    chipagent task submit "<request>" [--require-approval] [--output-dir DIR]
    chipagent task list
    chipagent task status <id>
    chipagent task pause <id>
    chipagent task resume <id>
    chipagent task approve <id>
    chipagent task rollback <id>

State is persisted per task (``<state_dir>/<id>.json``) so the panel survives
across invocations; the audit log (``<state_dir>/audit.jsonl``) records every
submit / approval / rollback decision with provenance.

Because the in-process orchestrator runs synchronously, the approval flow is
modelled as: a ``submit --require-approval`` whose gate fails records the task
as ``awaiting_approval`` (the artifact is **not** persisted); ``approve <id>``
re-runs the orchestrator with an approving callback, which then persists.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audit import AuditLog, EVT_APPROVE, EVT_PAUSE, EVT_REJECT, EVT_RESUME, EVT_ROLLBACK, EVT_SUBMIT
from .models import TaskStatus
from .multistep import MultiStepOrchestrator
from .state import TaskStateMachine, TaskStateError


def _now() -> str:
    """ISO timestamp. Pulled out so tests can monkeypatch / inject."""
    return datetime.now(timezone.utc).isoformat()


class TaskPanel:
    """In-process task panel: submit / list / status / pause / resume / approve / rollback."""

    def __init__(
        self,
        state_dir: Optional[str] = None,
        *,
        audit_path: Optional[str] = None,
        orchestrator: Optional[MultiStepOrchestrator] = None,
        use_llm: bool = False,
    ) -> None:
        self.state_dir = Path(state_dir or "logs/tasks")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.audit = AuditLog(audit_path or str(self.state_dir / "audit.jsonl"))
        self._orch: Optional[MultiStepOrchestrator] = orchestrator
        self._use_llm = use_llm

    # ------------------------------------------------------------------
    # Orchestrator access
    # ------------------------------------------------------------------
    def _get_orch(self, require_approval: bool, approver) -> MultiStepOrchestrator:
        if self._orch is not None:
            self._orch.require_approval = require_approval
            self._orch.approver = approver
            return self._orch
        orch = MultiStepOrchestrator(use_llm=self._use_llm,
                                     require_approval=require_approval, approver=approver)
        self._orch = orch
        return orch

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------
    def submit(
        self,
        request: str,
        *,
        output_dir: Optional[str] = None,
        context_path: Optional[str] = None,
        context_dir: Optional[str] = None,
        require_approval: bool = False,
        approver=None,
    ) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex[:12]
        output_dir = output_dir or str(Path("generated") / task_id)
        # Record the submission first so the task exists even if the run fails.
        machine = TaskStateMachine(task_id=task_id, state_dir=str(self.state_dir))
        machine.transition(TaskStatus.RUNNING, detail=request)
        machine.set_context("request", request)
        machine.set_context("output_dir", output_dir)
        machine.set_context("require_approval", require_approval)
        self.audit.record(EVT_SUBMIT, task_id=task_id, timestamp=_now(),
                          detail={"request": request, "output_dir": output_dir,
                                  "require_approval": require_approval})

        orch = self._get_orch(require_approval, approver)
        result = orch.run(request, context_path=context_path, context_dir=context_dir,
                          output_dir=output_dir, require_approval=require_approval)

        # Approval gate refused -> mark awaiting approval, do not finalise.
        error = result.get("error") or ""
        if require_approval and "approval:" in error:
            machine.transition(TaskStatus.NOTIFYING, detail="awaiting approval")
            self.audit.record(EVT_REJECT, task_id=task_id, timestamp=_now(),
                              detail=error)
            result["status"] = "awaiting_approval"
        else:
            final = TaskStatus.COMPLETED if result.get("status") == TaskStatus.COMPLETED.value else TaskStatus.FAILED
            machine.transition(final, detail=result.get("error") or "done")
            self._record_artifacts(task_id, result, produced_by="multistep")

        machine.set_context("result_status", result.get("status"))
        self._save_task(task_id, request, output_dir, require_approval, result)
        result["task_id"] = task_id
        return result

    # ------------------------------------------------------------------
    # List / status
    # ------------------------------------------------------------------
    def list(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for p in sorted(self.state_dir.glob("*.task.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            out.append({"task_id": data.get("task_id"), "request": data.get("request"),
                        "status": data.get("status"), "output_dir": data.get("output_dir")})
        return out

    def status(self, task_id: str) -> Dict[str, Any]:
        p = self.state_dir / f"{task_id}.task.json"
        if not p.exists():
            raise FileNotFoundError(f"no such task: {task_id}")
        return json.loads(p.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Pause / resume (state-machine + audit only; the synchronous run is not
    # paused mid-flight, but the state is recorded for the panel + Temporal).
    # ------------------------------------------------------------------
    def pause(self, task_id: str) -> Dict[str, Any]:
        machine = TaskStateMachine.resume(task_id, str(self.state_dir))
        try:
            machine.transition(TaskStatus.NOTIFYING, detail="paused")
        except TaskStateError:
            # Already finished; pausing a done task is an audited no-op.
            pass
        self.audit.record(EVT_PAUSE, task_id=task_id, timestamp=_now())
        return self.status(task_id)

    def resume(self, task_id: str) -> Dict[str, Any]:
        machine = TaskStateMachine.resume(task_id, str(self.state_dir))
        try:
            machine.transition(TaskStatus.RUNNING, detail="resumed")
        except TaskStateError:
            pass
        self.audit.record(EVT_RESUME, task_id=task_id, timestamp=_now())
        return self.status(task_id)

    # ------------------------------------------------------------------
    # Approve: re-run the orchestrator with an approving callback so the
    # gate passes and the artifact is persisted.
    # ------------------------------------------------------------------
    def approve(self, task_id: str) -> Dict[str, Any]:
        task = self.status(task_id)
        if task.get("status") != "awaiting_approval":
            # Idempotent: approving a completed/failed task just records the decision.
            self.audit.record(EVT_APPROVE, task_id=task_id, timestamp=_now())
            return task
        self.audit.record(EVT_APPROVE, task_id=task_id, timestamp=_now())
        orch = self._get_orch(require_approval=True, approver=lambda _detail: "approve")
        result = orch.run(task["request"], output_dir=task["output_dir"],
                          require_approval=True)
        machine = TaskStateMachine.resume(task_id, str(self.state_dir))
        final = TaskStatus.COMPLETED if result.get("status") == TaskStatus.COMPLETED.value else TaskStatus.FAILED
        machine.transition(final, detail=result.get("error") or "approved")
        self._record_artifacts(task_id, result, produced_by="multistep:approved")
        self._save_task(task_id, task["request"], task["output_dir"], True, result)
        result["task_id"] = task_id
        return result

    # ------------------------------------------------------------------
    # Rollback: remove the persisted artifacts for the task + record audit.
    # ------------------------------------------------------------------
    def rollback(self, task_id: str) -> Dict[str, Any]:
        task = self.status(task_id)
        output_dir = Path(task.get("output_dir", ""))
        removed: List[str] = []
        if output_dir.exists():
            for fpath in output_dir.iterdir():
                if fpath.is_file():
                    fpath.unlink()
                    removed.append(fpath.name)
            try:
                output_dir.rmdir()
            except OSError:
                pass
        machine = TaskStateMachine.resume(task_id, str(self.state_dir))
        machine.transition(TaskStatus.FAILED, detail="rolled back")
        self.audit.record(EVT_ROLLBACK, task_id=task_id, timestamp=_now(),
                          detail={"removed": removed, "output_dir": str(output_dir)})
        task["status"] = "rolled_back"
        task["removed"] = removed
        self._save_task(task_id, task["request"], str(output_dir),
                        task.get("require_approval", False), task)
        return task

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _save_task(self, task_id, request, output_dir, require_approval, result):
        payload = {
            "task_id": task_id,
            "request": request,
            "output_dir": output_dir,
            "require_approval": require_approval,
            "status": result.get("status"),
            "error": result.get("error"),
            "artifacts": result.get("artifacts", {}),
            "checks": result.get("output", {}).get("checks", {}),
        }
        (self.state_dir / f"{task_id}.task.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    def _record_artifacts(self, task_id, result, *, produced_by):
        for key, path in (result.get("artifacts") or {}).items():
            self.audit.record_artifact(
                task_id=task_id, timestamp=_now(), artifact=key,
                produced_by=produced_by, inputs={"path": str(path)},
            )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ChipAgent task panel")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("submit", help="Submit a natural-language request")
    sp.add_argument("request")
    sp.add_argument("--output-dir", dest="output_dir")
    sp.add_argument("--context", dest="context_path")
    sp.add_argument("--context-dir", dest="context_dir")
    sp.add_argument("--require-approval", action="store_true")
    sp.add_argument("--state-dir", default=None)
    sp.add_argument("--use-llm", action="store_true")

    sub.add_parser("list", help="List submitted tasks").add_argument("--state-dir", default=None)
    s = sub.add_parser("status", help="Show one task")
    s.add_argument("task_id"); s.add_argument("--state-dir", default=None)
    p = sub.add_parser("pause", help="Pause a task")
    p.add_argument("task_id"); p.add_argument("--state-dir", default=None)
    r = sub.add_parser("resume", help="Resume a paused task")
    r.add_argument("task_id"); r.add_argument("--state-dir", default=None)
    a = sub.add_parser("approve", help="Approve an awaiting-approval task")
    a.add_argument("task_id"); a.add_argument("--state-dir", default=None)
    rb = sub.add_parser("rollback", help="Roll back a task's artifacts")
    rb.add_argument("task_id"); rb.add_argument("--state-dir", default=None)

    args = parser.parse_args()
    panel = TaskPanel(state_dir=args.state_dir, use_llm=getattr(args, "use_llm", False))

    if args.cmd == "submit":
        result = panel.submit(
            args.request, output_dir=args.output_dir,
            context_path=args.context_path, context_dir=args.context_dir,
            require_approval=args.require_approval,
        )
    elif args.cmd == "list":
        result = panel.list()
    elif args.cmd == "status":
        result = panel.status(args.task_id)
    elif args.cmd == "pause":
        result = panel.pause(args.task_id)
    elif args.cmd == "resume":
        result = panel.resume(args.task_id)
    elif args.cmd == "approve":
        result = panel.approve(args.task_id)
    elif args.cmd == "rollback":
        result = panel.rollback(args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
