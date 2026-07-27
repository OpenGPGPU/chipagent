"""Security audit logging."""
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from .auth import SecurityContext


@dataclass
class AuditEvent:
    """Security audit event."""
    timestamp: datetime
    user_id: Optional[str]
    username: Optional[str]
    role: Optional[str]
    action: str
    resource: str
    result: str  # success, failure, error
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class SecurityAuditLog:
    """Audit log for security events."""

    def __init__(self, log_path: Optional[Path] = None):
        self.log_path = log_path or Path("logs/security_audit.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.events: List[AuditEvent] = []

    def log_event(self, context: Optional[SecurityContext], action: str,
                  resource: str, result: str, **details):
        """Log a security event."""
        event = AuditEvent(
            timestamp=datetime.now(),
            user_id=context.user.user_id if context and context.user else None,
            username=context.user.username if context and context.user else None,
            role=context.user.role if context and context.user else None,
            action=action,
            resource=resource,
            result=result,
            ip_address=context.ip_address if context else None,
            user_agent=context.user_agent if context else None,
            details=details,
        )

        self.events.append(event)
        self._write_event(event)

    def log_authentication_attempt(self, username: str, success: bool,
                                    ip_address: Optional[str] = None):
        """Log an authentication attempt."""
        self.log_event(
            context=None,
            action="authentication",
            resource=f"user:{username}",
            result="success" if success else "failure",
            ip_address=ip_address,
            username=username,
        )

    def log_tool_access(self, context: SecurityContext, tool_name: str,
                        authorized: bool, **details):
        """Log tool access."""
        self.log_event(
            context=context,
            action="tool_access",
            resource=f"tool:{tool_name}",
            result="success" if authorized else "denied",
            **details,
        )

    def log_task_execution(self, context: SecurityContext, task_type: str,
                           success: bool, **details):
        """Log task execution."""
        self.log_event(
            context=context,
            action="task_execution",
            resource=f"task:{task_type}",
            result="success" if success else "failure",
            **details,
        )

    def log_permission_denied(self, context: SecurityContext, permission: str,
                               resource: str):
        """Log permission denied event."""
        self.log_event(
            context=context,
            action="permission_denied",
            resource=resource,
            result="denied",
            permission=permission,
        )

    def _write_event(self, event: AuditEvent):
        """Write event to log file."""
        event_dict = {
            "timestamp": event.timestamp.isoformat(),
            "user_id": event.user_id,
            "username": event.username,
            "role": event.role,
            "action": event.action,
            "resource": event.resource,
            "result": event.result,
            "ip_address": event.ip_address,
            "user_agent": event.user_agent,
            "details": event.details,
        }

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_dict, ensure_ascii=False) + "\n")

    def get_events(self, user_id: Optional[str] = None,
                   action: Optional[str] = None,
                   since: Optional[datetime] = None) -> List[AuditEvent]:
        """Get audit events with optional filtering."""
        events = self.events

        if user_id:
            events = [e for e in events if e.user_id == user_id]

        if action:
            events = [e for e in events if e.action == action]

        if since:
            events = [e for e in events if e.timestamp >= since]

        return events

    def get_suspicious_events(self) -> List[AuditEvent]:
        """Get potentially suspicious events (failed auth, permission denied, etc.)."""
        return [
            e for e in self.events
            if e.result in ["failure", "denied", "error"]
        ]


# Global audit log instance
_audit_log = None


def get_audit_log() -> SecurityAuditLog:
    """Get the global audit log instance."""
    global _audit_log
    if _audit_log is None:
        _audit_log = SecurityAuditLog()
    return _audit_log
