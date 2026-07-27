"""Workflow state management (Section 3.4 / 10.6).

Phase 1 uses a lightweight in-process state machine that mirrors the states
Temporal would expose (submitted -> running -> completed | failed) and
persists each transition to disk so a run is inspectable and resumable. The
plan explicitly allows starting with a simple state machine ("先用简单状态
机完成流程"); the :class:`TaskStateMachine` here is the seam where a real
Temporal worker would slot in later.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .models import TaskStatus


# Valid forward transitions; anything not listed is rejected.
# Phase 2 (§4.2) adds the RETRYING/VERIFYING/NOTIFYING control states. They
# are intermediate: a task always re-enters RUNNING from them and may still
# terminate via COMPLETED/FAILED. The legacy submitted->running->completed|
# failed path is a strict subset, so phase-1 callers are unaffected.
_TRANSITIONS: Dict[TaskStatus, set] = {
    TaskStatus.SUBMITTED: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.RUNNING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.RETRYING,
        TaskStatus.VERIFYING,
        TaskStatus.NOTIFYING,
    },
    TaskStatus.RETRYING: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.VERIFYING: {TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.FAILED},
    TaskStatus.NOTIFYING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.COMPLETED},
    TaskStatus.COMPLETED: {TaskStatus.FAILED},  # rollback: a completed task can be rolled back
    TaskStatus.FAILED: {TaskStatus.RUNNING, TaskStatus.RETRYING},  # allow retry from failed
}


class TaskStateError(RuntimeError):
    """Raised on an illegal state transition."""


class TaskStateMachine:
    """A resumable, persistable task state machine."""

    def __init__(self, task_id: Optional[str] = None, state_dir: Optional[str] = None) -> None:
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self._state_dir = Path(state_dir) if state_dir else None
        self._status = TaskStatus.SUBMITTED
        self._history: list[Dict[str, Any]] = [{"status": self._status.value}]
        self._context: Dict[str, Any] = {}

    @property
    def status(self) -> TaskStatus:
        return self._status

    @property
    def history(self) -> list[Dict[str, Any]]:
        return list(self._history)

    def transition(self, target: TaskStatus, detail: Any = None) -> None:
        allowed = _TRANSITIONS.get(self._status, set())
        if target not in allowed and target is not self._status:
            raise TaskStateError(
                f"illegal transition {self._status.value} -> {target.value}"
            )
        self._status = target
        entry = {"status": target.value, "detail": detail}
        self._history.append(entry)
        self._persist()

    def set_context(self, key: str, value: Any) -> None:
        self._context[key] = value
        self._persist()

    def get_context(self, key: str, default: Any = None) -> Any:
        return self._context.get(key, default)

    def to_snapshot(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self._status.value,
            "history": self._history,
            "context": self._context,
        }

    def _persist(self) -> None:
        if self._state_dir is None:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / f"{self.task_id}.json"
        path.write_text(json.dumps(self.to_snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def resume(cls, task_id: str, state_dir: str) -> "TaskStateMachine":
        """Reconstruct a machine from a persisted snapshot (Temporal-style recovery)."""
        path = Path(state_dir) / f"{task_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"no persisted state for task {task_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        machine = cls(task_id=task_id, state_dir=state_dir)
        machine._status = TaskStatus(data["status"])
        machine._history = data.get("history", [])
        machine._context = data.get("context", {})
        return machine
