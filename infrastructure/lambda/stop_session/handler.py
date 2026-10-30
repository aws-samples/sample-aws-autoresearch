"""
stop_session: Stop the AI Researcher instance (and the GPU worker, for safety),
and mark the project paused. This is the "session over, stop spending" control.
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
    if not is_admin(project, caller(event)):   # stop = write action → owner/admin only
        return forbidden()

    # Stop the researcher and every worker (guaranteed no spend).
    worker_ids = project.get("worker_instance_ids") or []
    to_stop = [i for i in ([project.get("researcher_instance_id")] + list(worker_ids)) if i]
    if to_stop:
        try:
            ec2.stop_instances(InstanceIds=to_stop)
        except Exception as e:  # noqa: BLE001 - best-effort stop
            return _resp(500, {"error": f"Failed to stop instances: {e}"})

    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"project_id": project_id},
        UpdateExpression="SET #s = :paused, stopped_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":paused": "paused", ":now": now},
    )

    return _resp(200, {"project_id": project_id, "status": "paused", "stopped": to_stop})
