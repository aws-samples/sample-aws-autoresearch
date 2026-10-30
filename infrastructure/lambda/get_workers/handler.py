"""
get_workers: live power state of a project's GPU worker instances (for the dashboard).

Reads the project's worker_instance_ids from the Projects table and describes them via EC2, so the
UI can show a per-worker state list (workers are stopped by default and powered on independently
for the duration of each concurrent experiment). Read-only.

Returns: {"project_id", "workers": [{instance_id, state, instance_type, name}], "count"}
where state is the live EC2 state (running | stopped | pending | stopping | shutting-down |
terminated | unknown).
"""

import json
import os
import boto3

dynamodb = boto3.resource("dynamodb")
ec2 = boto3.client("ec2")

PROJECTS_TABLE = os.environ["PROJECTS_TABLE"]


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def _describe_one(iid, fallback_type):
    """Describe a single instance, tolerating a not-found (terminated + aged-out) id."""
    try:
        r = ec2.describe_instances(InstanceIds=[iid])
        inst = r["Reservations"][0]["Instances"][0]
        name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), None)
        return {"instance_id": iid, "state": inst["State"]["Name"],
                "instance_type": inst.get("InstanceType", fallback_type), "name": name}
    except Exception:  # noqa: BLE001 — InvalidInstanceID.NotFound, etc.
        return {"instance_id": iid, "state": "unknown", "instance_type": fallback_type, "name": None}


def handler(event, context):
    project_id = (event.get("pathParameters") or {}).get("projectId")
    if not project_id:
        return _resp(400, {"error": "projectId required"})

    item = dynamodb.Table(PROJECTS_TABLE).get_item(Key={"project_id": project_id}).get("Item") or {}
    fallback_type = item.get("worker_instance_type")

    ids = item.get("worker_instance_ids") or []
    ids = [i for i in ids if i and i != "None"]

    if not ids:
        return _resp(200, {"project_id": project_id, "workers": [], "count": 0})

    # Describe per-id so one terminated/missing worker doesn't blank the whole list, and so the
    # returned order matches worker_instance_ids (w0, w1, …).
    workers = [_describe_one(iid, fallback_type) for iid in ids]
    return _resp(200, {"project_id": project_id, "workers": workers, "count": len(workers)})
