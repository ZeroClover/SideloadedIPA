"""Well-known entitlement key names shared by every consuming layer.

Domain owns this vocabulary because it is the lowest layer the domain policy,
signing validation, verification comparison, Apple planning, and tooling
modules may all depend on. The module is deliberately constants-only and is
not re-exported through ``domain/__init__.py``; consumers import it directly
so the stage architecture guard keeps working.
"""

APPLICATION_IDENTIFIER = "application-identifier"
TEAM_IDENTIFIER = "com.apple.developer.team-identifier"
KEYCHAIN_ACCESS_GROUPS = "keychain-access-groups"
APPLICATION_GROUPS = "com.apple.security.application-groups"
GET_TASK_ALLOW = "get-task-allow"
