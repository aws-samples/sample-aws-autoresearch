"""
team: per-project team management (owner + admins + join requests).

Access model (development-plan.md): everyone can READ any project; owner/admins have WRITE access;
only the owner can delete. This function manages who the admins are:

- POST   /projects/{id}/team/requests          any authed  -> request to join (write a pending row)
- GET    /projects/{id}/team/requests           owner/admin -> list pending requests (+ owner/admins)
- POST   /projects/{id}/team/members            owner/admin -> admit {sub}: add to admins, clear request
- DELETE /projects/{id}/team/members/{sub}       owner       -> remove an admin

`owner`/`admins` live on the project record (the UI already fetches it for the badge); only the
pending-request list needs a call. No Cognito groups — membership is just owner + the admins map.
"""

import json
import os
import boto3
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key
from ar_authz import caller, is_admin, is_owner, forbidden

dynamodb = boto3.resource("dynamodb")
PROJECTS_TABLE = os.environ["PROJECTS_TABLE"]
TEAM_REQUESTS_TABLE = os.environ["TEAM_REQUESTS_TABLE"]


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def handler(event, context):
    project_id = (event.get("pathParameters") or {}).get("projectId", "")
    route = event.get("routeKey", "")            # e.g. "POST /projects/{projectId}/team/requests"
    who = caller(event)

    projects = dynamodb.Table(PROJECTS_TABLE)
    requests = dynamodb.Table(TEAM_REQUESTS_TABLE)
    project = projects.get_item(Key={"project_id": project_id}).get("Item")
    if not project:
        return _resp(404, {"error": "Project not found"})

    # --- has the caller already requested? (any authenticated user, so the UI can hide the button) ---
    if route.endswith("/team/requests/mine") and route.startswith("GET"):
        req = requests.get_item(Key={"project_id": project_id, "user_sub": who["sub"]}).get("Item")
        return _resp(200, {"requested": bool(req), "role": (
            "owner" if is_owner(project, who) else "admin" if is_admin(project, who) else "viewer")})

    # --- request to join (any authenticated user) ---
    if route.endswith("/team/requests") and route.startswith("POST"):
        if is_admin(project, who):
            return _resp(400, {"error": "You already have access to this project."})
        note = (json.loads(event.get("body") or "{}").get("note") or "").strip()
        requests.put_item(Item={
            "project_id": project_id, "user_sub": who["sub"], "username": who["username"],
            "note": note, "status": "pending",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        })
        return _resp(201, {"project_id": project_id, "status": "pending"})

    # --- list pending requests (owner/admin) ---
    if route.endswith("/team/requests") and route.startswith("GET"):
        if not is_admin(project, who):
            return forbidden()
        items = requests.query(KeyConditionExpression=Key("project_id").eq(project_id)).get("Items", [])
        return _resp(200, {
            "owner": project.get("owner", ""), "owner_username": project.get("owner_username", ""),
            "admins": project.get("admins", {}) or {}, "requests": items,
        })

    # --- admit a requester as admin (owner/admin) ---
    if route.endswith("/team/members") and route.startswith("POST"):
        if not is_admin(project, who):
            return forbidden()
        sub = (json.loads(event.get("body") or "{}").get("sub") or "").strip()
        if not sub:
            return _resp(400, {"error": "Provide the 'sub' of the user to admit."})
        req = requests.get_item(Key={"project_id": project_id, "user_sub": sub}).get("Item")
        username = (req or {}).get("username", "")
        # admins is a Map {sub: username}; the record always has it (create_project/backfill).
        projects.update_item(
            Key={"project_id": project_id},
            UpdateExpression="SET admins.#s = :u",
            ExpressionAttributeNames={"#s": sub},
            ExpressionAttributeValues={":u": username},
        )
        requests.delete_item(Key={"project_id": project_id, "user_sub": sub})
        return _resp(200, {"project_id": project_id, "admitted": sub, "username": username})

    # --- remove an admin (owner only) ---
    if route.endswith("/team/members/{sub}") and route.startswith("DELETE"):
        if not is_owner(project, who):
            return forbidden("Only the owner can change admins.")
        sub = (event.get("pathParameters") or {}).get("sub", "")
        projects.update_item(
            Key={"project_id": project_id},
            UpdateExpression="REMOVE admins.#s",
            ExpressionAttributeNames={"#s": sub},
        )
        requests.delete_item(Key={"project_id": project_id, "user_sub": sub})  # also clear any request
        return _resp(200, {"project_id": project_id, "removed": sub})

    return _resp(404, {"error": f"Unknown route {route}"})
