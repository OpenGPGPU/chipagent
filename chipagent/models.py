"""Data models for the ChipAgent phase 1 prototype.

These plain dataclasses define the structured objects that flow between the
interaction, parsing, orchestration, execution, repository and validation
layers. They intentionally avoid heavy framework dependencies so the prototype
stays easy to inspect and serialise.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    """Lifecycle states for a task, mirroring the Temporal-style states the
    phase 1 plan calls out: submitted -> running -> completed | failed.

    Phase 2 (§4.2 state machine) extends the lifecycle with the intermediate
    control states the design spec §4.2 lists: ``retrying`` (an activity is
    being retried after a transient failure), ``verifying`` (a validation /
    simulation / alignment gate is running), and ``notifying`` (waiting on or
    broadcasting a human approval / rollback decision). They are optional
    intermediate states — the legacy ``submitted -> running -> completed |
    failed`` path is unchanged, so existing callers and tests keep working.
    """

    SUBMITTED = "submitted"
    RUNNING = "running"
    RETRYING = "retrying"
    VERIFYING = "verifying"
    NOTIFYING = "notifying"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class LogEntry:
    """A single execution-log record. Logs are emitted by every workflow node
    so the run is fully traceable end-to-end."""

    step: str
    status: str
    detail: Any = None
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskRequest:
    """Raw user input before parsing."""

    request: str
    context_path: Optional[str] = None
    context_dir: Optional[str] = None
    output_dir: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskObject:
    """Structured task produced by the parsing layer."""

    task_type: str
    module_name: Optional[str]
    description: str
    interface: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    output_requirements: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillOutput:
    """Result returned by a Skill execution node."""

    code: str
    design_notes: str = ""
    checks: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactPaths:
    """Filesystem locations of persisted artifacts."""

    code_path: str = ""
    report_path: str = ""
    log_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowState:
    """Mutable state threaded through the LangGraph workflow. LangGraph reads
    the dict form of this object as the graph state."""

    request: str = ""
    task: Dict[str, Any] = field(default_factory=dict)
    context: str = ""
    code: str = ""
    design_notes: str = ""
    checks: Dict[str, Any] = field(default_factory=dict)
    logs: List[Dict[str, Any]] = field(default_factory=list)
    status: str = TaskStatus.RUNNING.value
    artifacts: Dict[str, Any] = field(default_factory=dict)
    output_dir: Optional[str] = None
    context_path: Optional[str] = None
    context_dir: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
