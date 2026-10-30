"""
submit_guidance: Write a human proposal/redirection to the Guidance inbox.

The always-on AI Researcher drains unprocessed guidance at the top of each loop
iteration (there is no orchestrator-wake model). guidance_id is time-sortable so
the agent processes items in submission order.
"""

import json
import os
import uuid
import boto3
from datetime import datetime, timezone
from ar_authz import caller, is_admin, forbidden

dynamodb = boto3.resource("dynamodb")
GUIDANCE_TABLE = os.environ["GUIDANCE_TABLE"]
PROJECTS_TABLE = os.environ["PROJECTS_TABLE"]


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def handler(event, context):
    project_id = event["pathParameters"]["projectId"]

    # Guidance is injected into the autonomous agent loop, and write_paper changes behavior — both are
    # write actions, so require owner/admin (a cross-tenant agent-hijack primitive otherwise).
    project = dynamodb.Table(PROJECTS_TABLE).get_item(Key={"project_id": project_id}).get("Item")
    if not project:
        return _resp(404, {"error": "Project not found"})
    if not is_admin(project, caller(event)):
        return forbidden()

    body = json.loads(event.get("body", "{}"))
    text = (body.get("text") or "").strip()
    write_paper = body.get("write_paper")   # optional: toggle the paper flag from the guidance box

    if not text and write_paper is None:
        return _resp(400, {"error": "Provide 'text' (guidance) and/or 'write_paper' (bool)"})

    result = {"project_id": project_id}

    # Optionally set the write_paper flag (lets the human turn paper-writing on/off after creation).
    if write_paper is not None:
        dynamodb.Table(PROJECTS_TABLE).update_item(
            Key={"project_id": project_id},
            UpdateExpression="SET write_paper = :w",
            ExpressionAttributeValues={":w": bool(write_paper)},
        )
        result["write_paper"] = bool(write_paper)

    # Append a guidance item only when there is actual text.
    if text:
        now = datetime.now(timezone.utc)
        # ISO timestamp prefix keeps items sorted; short random suffix avoids collisions.
        guidance_id = f"{now.isoformat()}#{uuid.uuid4().hex[:8]}"
        dynamodb.Table(GUIDANCE_TABLE).put_item(
            Item={
                "project_id": project_id,
                "guidance_id": guidance_id,
                "text": text,
                "author": body.get("author", "human"),
                "processed": False,
                "created_at": now.isoformat(),
            }
        )
        result.update({"guidance_id": guidance_id, "processed": False})

    return _resp(201, result)
