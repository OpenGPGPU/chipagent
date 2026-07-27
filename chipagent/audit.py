"""Structured audit log + artifact provenance (Phase 2 Step 8 / §1.8 §7).

Kept **separate** from the execution log (``logging_utils``): the execution
log records *what each workflow node did*; the audit log records *who
approved/rolled back what, and which Skill/tool/commit produced which
artifact* — the non-repudiable trail the design spec §1.8 (fourth layer) and
§7 (security) require.

The store is JSONL: one self-contained record per line, so a partial write
never corrupts earlier records and the file can be tailed/grepped directly.
Each record carries a monotonically increasing sequence number and an ISO
timestamp (the timestamp is taken on the caller side and passed in, so the
audit log stays deterministic and testable without a wall clock).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# Event types the workflow / task panel emit.
EVT_SUBMIT = "submit"
EVT_ARTIFACT = "artifact"      # an artifact was produced (provenance)
EVT_APPROVE = "approve"        # human approval decision
EVT_REJECT = "reject"
EVT_ROLLBACK = "rollback"
EVT_RETRY = "retry"
EVT_PAUSE = "pause"
EVT_RESUME = "resume"
EVT_COMPLETE = "complete"
EVT_FAIL = "fail"


@dataclass
class AuditRecord:
    """One audit-log entry. ``detail`` carries event-specific payload."""

    seq: int
    timestamp: str
    event: str
    task_id: str
    actor: str = "system"
    artifact: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)
    detail: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AuditLog:
    """Append-only JSONL audit log with simple query.

    Parameters
    ----------
    path:
        Path to the JSONL file. Created on first append.
    actor:
        Default actor for records that do not specify one (e.g. the workflow
        itself). The task panel overrides this per-call with the user.
    """

    def __init__(self, path: str, actor: str = "system") -> None:
        self.path = Path(path)
        self._actor = actor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Touch the file so existence checks work even before the first record.
        self.path.touch(exist_ok=True)

    @property
    def next_seq(self) -> int:
        """Next sequence number = (count of existing records) + 1."""
        return self._count_lines() + 1

    def record(
        self,
        event: str,
        *,
        task_id: str,
        timestamp: str,
        actor: Optional[str] = None,
        artifact: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
        detail: Any = None,
    ) -> AuditRecord:
        rec = AuditRecord(
            seq=self.next_seq,
            timestamp=timestamp,
            event=event,
            task_id=task_id,
            actor=actor or self._actor,
            artifact=artifact,
            provenance=provenance or {},
            detail=detail,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict(), ensure_ascii=False, default=str))
            fh.write("\n")
        return rec

    # ------------------------------------------------------------------
    # Convenience: record an artifact's provenance in one call.
    # ------------------------------------------------------------------
    def record_artifact(
        self,
        *,
        task_id: str,
        timestamp: str,
        artifact: str,
        produced_by: str,
        commit: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        actor: Optional[str] = None,
    ) -> AuditRecord:
        return self.record(
            EVT_ARTIFACT,
            task_id=task_id,
            timestamp=timestamp,
            artifact=artifact,
            actor=actor,
            provenance={"produced_by": produced_by, "commit": commit, "inputs": inputs or {}},
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def query(
        self,
        *,
        task_id: Optional[str] = None,
        event: Optional[str] = None,
        artifact: Optional[str] = None,
    ) -> List[AuditRecord]:
        out: List[AuditRecord] = []
        for rec in self._read_all():
            if task_id and rec.task_id != task_id:
                continue
            if event and rec.event != event:
                continue
            if artifact and rec.artifact != artifact:
                continue
            out.append(rec)
        return out

    def _read_all(self) -> List[AuditRecord]:
        records: List[AuditRecord] = []
        if not self.path.exists():
            return records
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append(AuditRecord(**d))
        return records

    def _count_lines(self) -> int:
        if not self.path.exists():
            return 0
        n = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                n += 1
        return n
