"""Base agent class for multi-agent collaboration."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from .shared_memory import SharedMemory


@dataclass
class AgentResult:
    """Result from an agent execution."""
    success: bool
    artifacts: Dict[str, Any] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """Base class for all agents."""

    def __init__(self, name: str, role: str, shared_memory: Optional[SharedMemory] = None):
        self.name = name
        self.role = role
        self.shared_memory = shared_memory or SharedMemory()

    @abstractmethod
    def execute(self, task: Dict[str, Any]) -> AgentResult:
        """Execute a task and return result."""
        pass

    def store_artifact(self, name: str, content: Any, **metadata):
        """Store an artifact in shared memory."""
        self.shared_memory.store_artifact(name, content, self.name, **metadata)

    def get_artifact(self, name: str) -> Optional[Any]:
        """Retrieve an artifact from shared memory."""
        artifact = self.shared_memory.get_artifact(name)
        return artifact.content if artifact else None

    def send_message(self, recipient: str, message: str, **metadata):
        """Send a message to another agent."""
        self.shared_memory.send_message(self.name, recipient, message, **metadata)

    def get_messages(self) -> List[Dict]:
        """Get messages addressed to this agent."""
        return self.shared_memory.get_messages(recipient=self.name)

    def record_decision(self, decision: str, reasoning: str, **metadata):
        """Record a decision made by this agent."""
        self.shared_memory.record_decision(self.name, decision, reasoning, **metadata)
