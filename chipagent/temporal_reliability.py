"""Phase 3 Temporal reliability enhancements.

Adds heartbeat monitoring, checkpoint management, and advanced timeout handling
for long-running Phase 3 activities (synthesis, physical design, knowledge indexing).
"""
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ActivityCheckpoint:
    """Checkpoint for long-running activities."""
    activity_name: str
    workflow_id: str
    step: str
    progress: float  # 0.0 to 1.0
    state: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    estimated_remaining_seconds: Optional[int] = None


@dataclass
class HeartbeatStatus:
    """Heartbeat status for an activity."""
    activity_name: str
    workflow_id: str
    last_heartbeat: datetime
    is_alive: bool
    progress: float
    message: str = ""


class CheckpointManager:
    """Manages checkpoints for long-running activities."""

    def __init__(self, checkpoint_dir: Path = None):
        self.checkpoint_dir = checkpoint_dir or Path("checkpoints")
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, checkpoint: ActivityCheckpoint):
        """Save a checkpoint to disk."""
        checkpoint_file = self._get_checkpoint_path(checkpoint.workflow_id, checkpoint.activity_name)
        data = {
            "activity_name": checkpoint.activity_name,
            "workflow_id": checkpoint.workflow_id,
            "step": checkpoint.step,
            "progress": checkpoint.progress,
            "state": checkpoint.state,
            "timestamp": checkpoint.timestamp.isoformat(),
            "estimated_remaining_seconds": checkpoint.estimated_remaining_seconds,
        }
        checkpoint_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def load_checkpoint(self, workflow_id: str, activity_name: str) -> Optional[ActivityCheckpoint]:
        """Load a checkpoint from disk."""
        checkpoint_file = self._get_checkpoint_path(workflow_id, activity_name)
        if not checkpoint_file.exists():
            return None

        data = json.loads(checkpoint_file.read_text())
        return ActivityCheckpoint(
            activity_name=data["activity_name"],
            workflow_id=data["workflow_id"],
            step=data["step"],
            progress=data["progress"],
            state=data.get("state", {}),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            estimated_remaining_seconds=data.get("estimated_remaining_seconds"),
        )

    def delete_checkpoint(self, workflow_id: str, activity_name: str):
        """Delete a checkpoint after successful completion."""
        checkpoint_file = self._get_checkpoint_path(workflow_id, activity_name)
        if checkpoint_file.exists():
            checkpoint_file.unlink()

    def list_checkpoints(self, workflow_id: Optional[str] = None) -> list:
        """List all checkpoints, optionally filtered by workflow_id."""
        checkpoints = []
        for checkpoint_file in self.checkpoint_dir.glob("*.json"):
            data = json.loads(checkpoint_file.read_text())
            if workflow_id is None or data.get("workflow_id") == workflow_id:
                checkpoints.append(ActivityCheckpoint(
                    activity_name=data["activity_name"],
                    workflow_id=data["workflow_id"],
                    step=data["step"],
                    progress=data["progress"],
                    state=data.get("state", {}),
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    estimated_remaining_seconds=data.get("estimated_remaining_seconds"),
                ))
        return checkpoints

    def _get_checkpoint_path(self, workflow_id: str, activity_name: str) -> Path:
        """Get the path for a checkpoint file."""
        return self.checkpoint_dir / f"{workflow_id}_{activity_name}.json"


class HeartbeatMonitor:
    """Monitors heartbeats from activities."""

    def __init__(self, timeout_seconds: int = 60):
        self.timeout_seconds = timeout_seconds
        self.heartbeats: Dict[str, HeartbeatStatus] = {}

    def record_heartbeat(self, activity_name: str, workflow_id: str,
                         progress: float, message: str = ""):
        """Record a heartbeat from an activity."""
        key = f"{workflow_id}:{activity_name}"
        self.heartbeats[key] = HeartbeatStatus(
            activity_name=activity_name,
            workflow_id=workflow_id,
            last_heartbeat=datetime.now(),
            is_alive=True,
            progress=progress,
            message=message,
        )

    def check_health(self, workflow_id: Optional[str] = None) -> Dict[str, HeartbeatStatus]:
        """Check health of all activities, marking stale ones as dead."""
        now = datetime.now()
        result = {}

        for key, status in self.heartbeats.items():
            if workflow_id and status.workflow_id != workflow_id:
                continue

            # Check if heartbeat is stale
            age = (now - status.last_heartbeat).total_seconds()
            is_alive = age < self.timeout_seconds

            result[key] = HeartbeatStatus(
                activity_name=status.activity_name,
                workflow_id=status.workflow_id,
                last_heartbeat=status.last_heartbeat,
                is_alive=is_alive,
                progress=status.progress,
                message=status.message,
            )

        return result

    def get_stale_activities(self, workflow_id: Optional[str] = None) -> list:
        """Get list of activities with stale heartbeats."""
        health = self.check_health(workflow_id)
        return [status for status in health.values() if not status.is_alive]

    def clear_heartbeat(self, activity_name: str, workflow_id: str):
        """Clear heartbeat after activity completion."""
        key = f"{workflow_id}:{activity_name}"
        if key in self.heartbeats:
            del self.heartbeats[key]


class ActivityTimeoutManager:
    """Manages timeouts for different activity types."""

    def __init__(self):
        # Default timeouts for Phase 3 activities (in seconds)
        self.timeouts = {
            # Synthesis activities
            "run_synthesis": 300,  # 5 minutes
            "analyze_timing": 180,  # 3 minutes
            "optimize_area": 300,  # 5 minutes
            "analyze_power": 180,  # 3 minutes
            "run_formality": 600,  # 10 minutes

            # Physical design activities
            "create_floorplan": 120,  # 2 minutes
            "run_placement": 600,  # 10 minutes
            "run_cts": 300,  # 5 minutes
            "run_routing": 600,  # 10 minutes
            "run_drc_check": 180,  # 3 minutes
            "run_lvs_check": 180,  # 3 minutes

            # Knowledge activities
            "query_knowledge_base": 30,  # 30 seconds
            "search_code_examples": 30,  # 30 seconds
            "consult_architecture": 60,  # 1 minute
            "diagnose_issue": 60,  # 1 minute
            "generate_documentation": 120,  # 2 minutes
            "search_software_reference": 30,  # 30 seconds
            "consult_sw_hw_co_design": 60,  # 1 minute
            "build_knowledge_index": 600,  # 10 minutes

            # DPI co-simulation
            "run_dpi_cosim": 300,  # 5 minutes
        }

    def get_timeout(self, activity_name: str) -> int:
        """Get timeout for an activity."""
        return self.timeouts.get(activity_name, 120)  # Default 2 minutes

    def set_timeout(self, activity_name: str, timeout_seconds: int):
        """Set custom timeout for an activity."""
        self.timeouts[activity_name] = timeout_seconds

    def get_all_timeouts(self) -> Dict[str, int]:
        """Get all configured timeouts."""
        return self.timeouts.copy()


# Global instances
_checkpoint_manager = None
_heartbeat_monitor = None
_timeout_manager = None


def get_checkpoint_manager() -> CheckpointManager:
    """Get the global checkpoint manager."""
    global _checkpoint_manager
    if _checkpoint_manager is None:
        _checkpoint_manager = CheckpointManager()
    return _checkpoint_manager


def get_heartbeat_monitor() -> HeartbeatMonitor:
    """Get the global heartbeat monitor."""
    global _heartbeat_monitor
    if _heartbeat_monitor is None:
        _heartbeat_monitor = HeartbeatMonitor()
    return _heartbeat_monitor


def get_timeout_manager() -> ActivityTimeoutManager:
    """Get the global timeout manager."""
    global _timeout_manager
    if _timeout_manager is None:
        _timeout_manager = ActivityTimeoutManager()
    return _timeout_manager
