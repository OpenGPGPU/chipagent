"""Shared memory for multi-agent collaboration."""
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Artifact:
    """Represents an artifact produced by an agent."""
    name: str
    content: Any
    producer: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SharedMemory:
    """Shared memory space for multi-agent collaboration."""

    def __init__(self):
        self.artifacts: Dict[str, Artifact] = {}
        self.messages: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []

    def store_artifact(self, name: str, content: Any, producer: str, **metadata):
        """Store an artifact in shared memory."""
        self.artifacts[name] = Artifact(
            name=name,
            content=content,
            producer=producer,
            metadata=metadata,
        )

    def get_artifact(self, name: str) -> Optional[Artifact]:
        """Retrieve an artifact by name."""
        return self.artifacts.get(name)

    def list_artifacts(self, producer: Optional[str] = None) -> List[Artifact]:
        """List all artifacts, optionally filtered by producer."""
        if producer:
            return [a for a in self.artifacts.values() if a.producer == producer]
        return list(self.artifacts.values())

    def send_message(self, sender: str, recipient: str, message: str, **metadata):
        """Send a message between agents."""
        self.messages.append({
            "sender": sender,
            "recipient": recipient,
            "message": message,
            "timestamp": datetime.now(),
            "metadata": metadata,
        })

    def get_messages(self, recipient: Optional[str] = None, sender: Optional[str] = None) -> List[Dict]:
        """Get messages, optionally filtered by recipient or sender."""
        messages = self.messages
        if recipient:
            messages = [m for m in messages if m["recipient"] == recipient]
        if sender:
            messages = [m for m in messages if m["sender"] == sender]
        return messages

    def record_decision(self, agent: str, decision: str, reasoning: str, **metadata):
        """Record a decision made by an agent."""
        self.decisions.append({
            "agent": agent,
            "decision": decision,
            "reasoning": reasoning,
            "timestamp": datetime.now(),
            "metadata": metadata,
        })

    def get_decisions(self, agent: Optional[str] = None) -> List[Dict]:
        """Get decisions, optionally filtered by agent."""
        if agent:
            return [d for d in self.decisions if d["agent"] == agent]
        return self.decisions

    def clear(self):
        """Clear all shared memory."""
        self.artifacts.clear()
        self.messages.clear()
        self.decisions.clear()
