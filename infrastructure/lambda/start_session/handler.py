"""
start_session: Start the AI Researcher instance and mark the project active.

Replaces the old `start_project` (no worker fleet, no event queue). Only the CPU
AI Researcher is started; it (via the run-experiment sub-agent) powers the GPU
worker on/off itself.
"""

import json
import os
import boto3
from datetime import datetime, timezone
from ar_authz import caller, is_admin, forbidden

dynamodb = boto3.resource("dynamodb")
ec2 = boto3.client("ec2")

PROJECTS_TABLE = os.environ["PROJECTS_TABLE"]


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def handler(event, context):
    project_id = event["pathParameters"]["projectId"]
    table = dynamodb.Table(PROJECTS_TABLE)
    project = table.get_item(Key={"project_id": project_id}).get("Item")
    if not project:
        return _resp(404, {"error": "Project not found"})
    if not is_admin(project, caller(event)):   # start = write action → owner/admin only
        return forbidden()

    if project.get("status") not in ("setup", "paused"):
        return _resp(400, {"error": f"Project is '{project.get('status')}', not 'setup'/'paused'"})

    researcher_id = project.get("researcher_instance_id")
    if not researcher_id:
        return _resp(500, {"error": "Project has no researcher_instance_id"})

    ec2.start_instances(InstanceIds=[researcher_id])

    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"project_id": project_id},
        UpdateExpression="SET #s = :active, started_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":active": "active", ":now": now},
    )

    return _resp(200, {
        "project_id": project_id,
        "status": "active",
        "message": "AI Researcher starting. It will begin its loop after bootstrap.",
    })
