"""
get_experiments: Returns all experiments for a project, optionally filtered by state.
Also handles GET /projects (list all projects) when no projectId is provided.
"""

import json
import os
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")

PROJECTS_TABLE = os.environ["PROJECTS_TABLE"]
EXPERIMENTS_TABLE = os.environ["EXPERIMENTS_TABLE"]


def handler(event, context):
    path_params = event.get("pathParameters") or {}
    project_id = path_params.get("projectId")

    # If no projectId, this is a list-all-projects request
    if not project_id:
        return _list_projects()

    table = dynamodb.Table(EXPERIMENTS_TABLE)
    response = table.query(KeyConditionExpression=Key("project_id").eq(project_id))
    experiments = response.get("Items", [])
    # Newest last (chronological).
    experiments.sort(key=lambda x: x.get("created_at", ""))

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"experiments": experiments, "count": len(experiments)}, default=str),
    }


def _list_projects():
    table = dynamodb.Table(PROJECTS_TABLE)
    response = table.scan()
    projects = response.get("Items", [])
    projects.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    # Annotate each project with how many experiments it has run (a COUNT query per
    # project) so the web app can show experiment progress at a glance.
    exp_table = dynamodb.Table(EXPERIMENTS_TABLE)
    for p in projects:
        pid = p.get("project_id")
        if not pid:
            continue
        try:
            c = exp_table.query(
                KeyConditionExpression=Key("project_id").eq(pid), Select="COUNT"
            )
            p["experiment_count"] = c.get("Count", 0)
        except Exception:  # noqa: BLE001 — never fail the list on a count error
            p["experiment_count"] = None

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"projects": projects, "count": len(projects)}, default=str),
    }
