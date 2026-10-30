"""Shared per-project authorization helpers (Lambda layer).

Access model (see development-plan.md): every authenticated user may READ any project; WRITE actions
(start/stop/guidance) require owner or admin; DELETE requires owner. Identity comes from the Cognito
JWT the HTTP API authorizer validates — `sub` (immutable user id) and `username`.
"""

import json


def caller(event):
    """The authenticated caller, from the JWT claims the HTTP API authorizer put on the event."""
    claims = (
        (event or {})
        .get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    ) or {}
    return {
        "sub": claims.get("sub", ""),
        "username": claims.get("username") or claims.get("cognito:username", ""),
    }


def is_owner(project, who):
    """True iff `who` created the project."""
    return bool(who.get("sub")) and who["sub"] == (project or {}).get("owner")


def is_admin(project, who):
    """True iff `who` has write access — the owner or a listed admin."""
    if not who.get("sub"):
        return False
    p = project or {}
    if who["sub"] == p.get("owner"):
        return True
    return who["sub"] in (p.get("admins") or {})


def forbidden(message="You do not have permission to perform this action on this project."):
    return {
        "statusCode": 403,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }
