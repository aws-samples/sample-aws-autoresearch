"""
delete_project: tear down one project's resources (the cleanup that used to be manual) — ASYNC.

Two modes in one function (mirrors create_project), so the UI never waits on a slow teardown:
- API mode (fast, <2s): mark the project `deleting`, self-invoke the async teardown, and return 202
  immediately. The web app shows a live "Deleting…" state and the row disappears when teardown
  finishes — so a customer watching for their credits to stop being spent gets real feedback, not a
  hung spinner.
- Teardown mode (async): terminate the researcher + worker instances, delete the code/wiki CodeCommit
  repos, the CloudWatch log group, the S3 data prefix (can be tens of thousands of objects), and the
  DynamoDB rows (project + experiments + guidance). This has the full Lambda timeout and no API
  Gateway 29s cap. Best-effort and idempotent — missing pieces are skipped.
"""

import json
import os
import re
import boto3
from boto3.dynamodb.conditions import Key
from decimal import Decimal
from ar_authz import caller, is_owner, forbidden

dynamodb = boto3.resource("dynamodb")

# Canonical project-id slug (matches create_project._slug). Enforced before any resource name is
# built from the id, so a caller can't smuggle wildcards/paths into repo/log-group deletes.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
codecommit = boto3.client("codecommit")
ec2 = boto3.client("ec2")
logs_client = boto3.client("logs")
s3 = boto3.client("s3")
lam = boto3.client("lambda")
iam = boto3.client("iam")
ssm = boto3.client("ssm")
SSM_CORE_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

PROJECTS_TABLE = os.environ["PROJECTS_TABLE"]
EXPERIMENTS_TABLE = os.environ["EXPERIMENTS_TABLE"]
GUIDANCE_TABLE = os.environ["GUIDANCE_TABLE"]
USERS_TABLE = os.environ.get("USERS_TABLE", "")
DATA_BUCKET = os.environ["DATA_BUCKET"]


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def handler(event, context):
    # Async teardown self-invoke carries {"_delete": true, "project_id": ...}.
    if isinstance(event, dict) and event.get("_delete"):
        return _teardown(event.get("project_id", ""))

    # API mode: flag the project deleting, kick off the async teardown, return at once.
    project_id = event["pathParameters"]["projectId"]
    if not _SLUG_RE.match(project_id or ""):
        return _resp(400, {"error": "Invalid project id"})

    # Delete is owner-only (destroys instances/repos/data). Load the project and check ownership.
    project = dynamodb.Table(PROJECTS_TABLE).get_item(Key={"project_id": project_id}).get("Item")
    if not project:
        return _resp(404, {"error": "Project not found"})
    if not is_owner(project, caller(event)):
        return forbidden("Only the project owner can delete this project.")

    try:
        dynamodb.Table(PROJECTS_TABLE).update_item(
            Key={"project_id": project_id},
            UpdateExpression="SET #s = :d",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":d": "deleting"},
        )
    except Exception:  # noqa: BLE001 - row may be gone (orphan cleanup); teardown still runs
        pass
    lam.invoke(FunctionName=context.function_name, InvocationType="Event",
               Payload=json.dumps({"_delete": True, "project_id": project_id}).encode("utf-8"))
    return _resp(202, {"project_id": project_id, "status": "deleting"})


def _teardown(project_id):
    removed = []

    # Read the project row up front (it's deleted at the end) to refund the owner's budget: the
    # unspent part of the reservation, max_budget − spent, goes back. Only for projects that reserved.
    project = dynamodb.Table(PROJECTS_TABLE).get_item(Key={"project_id": project_id}).get("Item") or {}
    if project.get("budget_reserved") and project.get("owner") and USERS_TABLE:
        try:
            m = Decimal(str(project.get("budget_usd", 0)))
            s = Decimal(str(project.get("spent_usd", 0)))
            refund = m - min(s, m)
            if refund > 0:
                dynamodb.Table(USERS_TABLE).update_item(
                    Key={"user_sub": project["owner"]},
                    UpdateExpression="ADD budget :r",
                    ExpressionAttributeValues={":r": refund},
                )
                removed.append(f"refunded ${refund}")
        except Exception as e:  # noqa: BLE001
            removed.append(f"refund: skipped ({e})")

    # 1. Terminate all instances tagged for this project (covers partial/failed provisioning too).
    try:
        resp = ec2.describe_instances(Filters=[
            {"Name": "tag:ProjectId", "Values": [project_id]},
            {"Name": "tag:ManagedBy", "Values": ["auto-research"]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ])
        ids = [i["InstanceId"] for r in resp.get("Reservations", []) for i in r.get("Instances", [])]
        if ids:
            ec2.terminate_instances(InstanceIds=ids)
            removed.append(f"{len(ids)} instance(s)")
    except Exception as e:  # noqa: BLE001
        removed.append(f"instances: skipped ({e})")

    # 2. CodeCommit repos.
    for repo in (f"{project_id}-code", f"{project_id}-wiki"):
        try:
            codecommit.delete_repository(repositoryName=repo)
            removed.append(repo)
        except Exception:  # noqa: BLE001
            pass

    # 3. Log group.
    try:
        logs_client.delete_log_group(logGroupName=f"/auto-research/{project_id}")
        removed.append("log group")
    except Exception:  # noqa: BLE001
        pass

    # 4. S3 data prefix + any provisioning payload.
    try:
        paginator = s3.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=DATA_BUCKET, Prefix=f"{project_id}/"):
            keys += [{"Key": o["Key"]} for o in page.get("Contents", [])]
        keys.append({"Key": f"_provisioning/{project_id}.json"})
        keys.append({"Key": f"_platform/settings/{project_id}.json"})  # per-project deny-rules bundle
        for i in range(0, len(keys), 1000):
            s3.delete_objects(Bucket=DATA_BUCKET, Delete={"Objects": keys[i:i + 1000], "Quiet": True})
        removed.append(f"{len(keys)} S3 object(s)")
    except Exception as e:  # noqa: BLE001
        removed.append(f"s3: skipped ({e})")

    # 5. Per-project IAM role + instance profile, SSH keypair, and SSM params (isolation GC).
    role_name = f"auto-research-proj-{project_id}"[:64]
    try:
        try:
            iam.remove_role_from_instance_profile(InstanceProfileName=role_name, RoleName=role_name)
        except Exception:  # noqa: BLE001
            pass
        try:
            iam.delete_instance_profile(InstanceProfileName=role_name)
        except Exception:  # noqa: BLE001
            pass
        try:
            iam.delete_role_policy(RoleName=role_name, PolicyName="project-scope")
        except Exception:  # noqa: BLE001
            pass
        try:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=SSM_CORE_POLICY_ARN)
        except Exception:  # noqa: BLE001
            pass
        try:
            iam.delete_role(RoleName=role_name)
            removed.append("iam role/profile")
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        removed.append(f"iam: skipped ({e})")
    for kn in (f"auto-research-{project_id}-worker",):
        try:
            ec2.delete_key_pair(KeyName=kn)
        except Exception:  # noqa: BLE001
            pass
    for param in (f"/auto-research/projects/{project_id}/worker-key",
                  f"/auto-research/projects/{project_id}/agent-token"):
        try:
            ssm.delete_parameter(Name=param)
        except Exception:  # noqa: BLE001
            pass
    removed.append("key + ssm params")

    # 6. DynamoDB rows.
    try:
        exp = dynamodb.Table(EXPERIMENTS_TABLE)
        for it in exp.query(KeyConditionExpression=Key("project_id").eq(project_id)).get("Items", []):
            exp.delete_item(Key={"project_id": project_id, "experiment_id": it["experiment_id"]})
        gud = dynamodb.Table(GUIDANCE_TABLE)
        for it in gud.query(KeyConditionExpression=Key("project_id").eq(project_id)).get("Items", []):
            gud.delete_item(Key={"project_id": project_id, "guidance_id": it["guidance_id"]})
        dynamodb.Table(PROJECTS_TABLE).delete_item(Key={"project_id": project_id})
        removed.append("db rows")
    except Exception as e:  # noqa: BLE001
        removed.append(f"db: skipped ({e})")

    return {"project_id": project_id, "deleted": removed}  # async invoke — result is logged, not returned to a client
