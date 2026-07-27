"""Authorization and role-based access control."""
from typing import List, Set, Optional
from enum import Enum
from dataclasses import dataclass, field
from .auth import SecurityContext, AuthenticationError


class Role(Enum):
    """User roles."""
    ADMIN = "admin"
    DESIGNER = "designer"
    VERIFIER = "verifier"
    SWENGINEER = "swengineer"
    VIEWER = "viewer"


class Permission(Enum):
    """Permissions for operations."""
    # Synthesis
    RUN_SYNTHESIS = "run_synthesis"
    ANALYZE_TIMING = "analyze_timing"
    OPTIMIZE_AREA = "optimize_area"
    ANALYZE_POWER = "analyze_power"
    RUN_FORMALITY = "run_formality"

    # Physical design
    CREATE_FLOORPLAN = "create_floorplan"
    RUN_PLACEMENT = "run_placement"
    RUN_CTS = "run_cts"
    RUN_ROUTING = "run_routing"
    RUN_DRC = "run_drc_check"
    RUN_LVS = "run_lvs_check"

    # Verification
    RUN_SIMULATION = "run_simulation"
    CHECK_ALIGNMENT = "check_register_alignment"
    CHECK_INTERFACE = "check_sw_hw_interface"
    ANALYZE_COVERAGE = "analyze_coverage"
    RUN_COSIM = "run_sw_hw_cosim"

    # Knowledge
    QUERY_KNOWLEDGE = "query_knowledge_base"
    SEARCH_EXAMPLES = "search_code_examples"
    CONSULT_ARCHITECTURE = "consult_architecture"
    DIAGNOSE_ISSUE = "diagnose_issue"

    # Management
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT_LOG = "view_audit_log"
    MANAGE_TASKS = "manage_tasks"


# Role to permission mapping
ROLE_PERMISSIONS = {
    Role.ADMIN: set(Permission),  # All permissions

    Role.DESIGNER: {
        Permission.RUN_SYNTHESIS,
        Permission.ANALYZE_TIMING,
        Permission.OPTIMIZE_AREA,
        Permission.ANALYZE_POWER,
        Permission.RUN_FORMALITY,
        Permission.CREATE_FLOORPLAN,
        Permission.RUN_PLACEMENT,
        Permission.RUN_CTS,
        Permission.RUN_ROUTING,
        Permission.RUN_DRC,
        Permission.RUN_LVS,
        Permission.RUN_SIMULATION,
        Permission.CHECK_ALIGNMENT,
        Permission.CHECK_INTERFACE,
        Permission.ANALYZE_COVERAGE,
        Permission.QUERY_KNOWLEDGE,
        Permission.SEARCH_EXAMPLES,
        Permission.CONSULT_ARCHITECTURE,
        Permission.DIAGNOSE_ISSUE,
        Permission.MANAGE_TASKS,
    },

    Role.VERIFIER: {
        Permission.RUN_SIMULATION,
        Permission.CHECK_ALIGNMENT,
        Permission.CHECK_INTERFACE,
        Permission.ANALYZE_COVERAGE,
        Permission.RUN_COSIM,
        Permission.QUERY_KNOWLEDGE,
        Permission.SEARCH_EXAMPLES,
        Permission.DIAGNOSE_ISSUE,
        Permission.MANAGE_TASKS,
    },

    Role.SWENGINEER: {
        Permission.CHECK_ALIGNMENT,
        Permission.CHECK_INTERFACE,
        Permission.RUN_COSIM,
        Permission.QUERY_KNOWLEDGE,
        Permission.SEARCH_EXAMPLES,
        Permission.DIAGNOSE_ISSUE,
        Permission.MANAGE_TASKS,
    },

    Role.VIEWER: {
        Permission.QUERY_KNOWLEDGE,
        Permission.SEARCH_EXAMPLES,
    },
}


@dataclass
class Authorizer:
    """Handles authorization checks."""

    def has_permission(self, context: SecurityContext, permission: Permission) -> bool:
        """Check if context has a specific permission."""
        if not context.is_authenticated():
            return False

        user_role = Role(context.user.role)
        user_permissions = ROLE_PERMISSIONS.get(user_role, set())

        return permission in user_permissions

    def require_permission(self, context: SecurityContext, permission: Permission):
        """Require a permission, raise error if not granted."""
        if not self.has_permission(context, permission):
            raise AuthenticationError(
                f"User {context.user.username} does not have permission: {permission.value}"
            )

    def get_user_permissions(self, context: SecurityContext) -> Set[Permission]:
        """Get all permissions for a user."""
        if not context.is_authenticated():
            return set()

        user_role = Role(context.user.role)
        return ROLE_PERMISSIONS.get(user_role, set())

    def can_access_tool(self, context: SecurityContext, tool_name: str) -> bool:
        """Check if user can access a specific tool."""
        # Map tool names to permissions
        tool_permission_map = {
            "run_synthesis": Permission.RUN_SYNTHESIS,
            "analyze_timing": Permission.ANALYZE_TIMING,
            "optimize_area": Permission.OPTIMIZE_AREA,
            "analyze_power": Permission.ANALYZE_POWER,
            "run_formality": Permission.RUN_FORMALITY,
            "create_floorplan": Permission.CREATE_FLOORPLAN,
            "run_placement": Permission.RUN_PLACEMENT,
            "run_cts": Permission.RUN_CTS,
            "run_routing": Permission.RUN_ROUTING,
            "run_drc_check": Permission.RUN_DRC,
            "run_lvs_check": Permission.RUN_LVS,
            "run_simulation": Permission.RUN_SIMULATION,
            "check_register_alignment": Permission.CHECK_ALIGNMENT,
            "check_sw_hw_interface": Permission.CHECK_INTERFACE,
            "analyze_coverage": Permission.ANALYZE_COVERAGE,
            "run_sw_hw_cosim": Permission.RUN_COSIM,
            "query_knowledge_base": Permission.QUERY_KNOWLEDGE,
            "search_code_examples": Permission.SEARCH_EXAMPLES,
            "consult_architecture": Permission.CONSULT_ARCHITECTURE,
            "diagnose_issue": Permission.DIAGNOSE_ISSUE,
        }

        permission = tool_permission_map.get(tool_name)
        if not permission:
            return False  # Unknown tool

        return self.has_permission(context, permission)


# Global authorizer instance
_authorizer = Authorizer()


def get_authorizer() -> Authorizer:
    """Get the global authorizer instance."""
    return _authorizer
