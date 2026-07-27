"""Structured execution logging for the phase 1 workflow.

Each workflow node appends a :class:`LogEntry`-shaped dict to the run's log
list. On completion the log is persisted as JSON next to the generated
artifacts, giving the traceability the phase 1 acceptance criteria require
("系统能够记录任务状态和执行日志").
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import LogEntry


def now_ts() -> str:
    """ISO-8601 UTC timestamp. Kept here so all nodes stamp logs uniformly."""
    return datetime.now(timezone.utc).isoformat()


def make_log(step: str, status: str, detail: Any = None) -> Dict[str, Any]:
    return LogEntry(step=step, status=status, detail=detail, timestamp=now_ts()).to_dict()


def append_log(logs: List[Dict[str, Any]], step: str, status: str, detail: Any = None) -> Dict[str, Any]:
    entry = make_log(step, status, detail)
    logs.append(entry)
    return entry


def write_log_file(log_dir: Optional[str], task_id: str, logs: List[Dict[str, Any]]) -> Optional[str]:
    """Persist the full execution log as a JSON file. Returns the path or None."""
    if not log_dir:
        return None
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    log_path = path / f"{task_id}.json"
    payload = {"task_id": task_id, "entries": logs}
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(log_path)
