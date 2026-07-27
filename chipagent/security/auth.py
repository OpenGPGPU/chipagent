"""Authentication and security context management."""
import os
import hashlib
import secrets
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


@dataclass
class User:
    """User information."""
    user_id: str
    username: str
    role: str
    api_key_hash: str
    created_at: datetime = field(default_factory=datetime.now)
    last_login: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SecurityContext:
    """Security context for current session."""
    user: Optional[User] = None
    authenticated: bool = False
    session_token: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    def is_authenticated(self) -> bool:
        """Check if context is authenticated."""
        return self.authenticated and self.user is not None

    def has_role(self, role: str) -> bool:
        """Check if user has specific role."""
        return self.is_authenticated() and self.user.role == role


class Authenticator:
    """Handles user authentication."""

    def __init__(self):
        self.users: Dict[str, User] = {}
        self.sessions: Dict[str, SecurityContext] = {}
        self._init_default_users()

    def _init_default_users(self):
        """Initialize default users for development."""
        # Admin user
        self.register_user(
            user_id="admin-001",
            username="admin",
            role="admin",
            api_key="chipagent-admin-key-2024",
        )

        # Designer user
        self.register_user(
            user_id="designer-001",
            username="designer",
            role="designer",
            api_key="chipagent-designer-key-2024",
        )

        # Verification engineer
        self.register_user(
            user_id="verifier-001",
            username="verifier",
            role="verifier",
            api_key="chipagent-verifier-key-2024",
        )

        # Software engineer
        self.register_user(
            user_id="sweng-001",
            username="swengineer",
            role="swengineer",
            api_key="chipagent-sweng-key-2024",
        )

    def register_user(self, user_id: str, username: str, role: str, api_key: str, **metadata):
        """Register a new user."""
        api_key_hash = self._hash_api_key(api_key)
        user = User(
            user_id=user_id,
            username=username,
            role=role,
            api_key_hash=api_key_hash,
            metadata=metadata,
        )
        self.users[user_id] = user

    def authenticate_with_api_key(self, api_key: str, ip_address: Optional[str] = None,
                                   user_agent: Optional[str] = None) -> SecurityContext:
        """Authenticate user with API key."""
        api_key_hash = self._hash_api_key(api_key)

        # Find user
        for user in self.users.values():
            if user.api_key_hash == api_key_hash:
                # Create session
                session_token = self._generate_session_token()
                user.last_login = datetime.now()

                context = SecurityContext(
                    user=user,
                    authenticated=True,
                    session_token=session_token,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

                self.sessions[session_token] = context
                return context

        raise AuthenticationError("Invalid API key")

    def validate_session(self, session_token: str) -> SecurityContext:
        """Validate a session token."""
        if session_token not in self.sessions:
            raise AuthenticationError("Invalid session token")

        context = self.sessions[session_token]
        if not context.authenticated:
            raise AuthenticationError("Session not authenticated")

        return context

    def revoke_session(self, session_token: str):
        """Revoke a session."""
        if session_token in self.sessions:
            del self.sessions[session_token]

    def _hash_api_key(self, api_key: str) -> str:
        """Hash an API key for storage."""
        return hashlib.sha256(api_key.encode()).hexdigest()

    def _generate_session_token(self) -> str:
        """Generate a secure session token."""
        return secrets.token_urlsafe(32)


# Global authenticator instance
_authenticator = Authenticator()


def get_authenticator() -> Authenticator:
    """Get the global authenticator instance."""
    return _authenticator


def require_auth(func):
    """Decorator to require authentication for a function."""
    def wrapper(*args, **kwargs):
        # Check if authentication is enabled
        if os.environ.get("CHIPAGENT_AUTH_ENABLED", "false").lower() != "true":
            return func(*args, **kwargs)

        # Get security context from kwargs or environment
        ctx = kwargs.get("security_context")
        if not ctx:
            api_key = os.environ.get("CHIPAGENT_API_KEY")
            if not api_key:
                raise AuthenticationError("No API key provided")

            authenticator = get_authenticator()
            ctx = authenticator.authenticate_with_api_key(api_key)
            kwargs["security_context"] = ctx

        # Verify authentication
        if not ctx.is_authenticated():
            raise AuthenticationError("Not authenticated")

        return func(*args, **kwargs)

    return wrapper
