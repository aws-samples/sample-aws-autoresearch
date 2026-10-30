"""
agent_api: the control-plane API the AI Researcher agent calls INSTEAD of touching DynamoDB directly.

The agent (researcher + run-experiment sub-agent, on EC2) no longer has DynamoDB permissions. It
invokes THIS Lambda (via `aws lambda invoke`, authenticated by its instance role's scoped
lambda:InvokeFunction) with a JSON payload `{"action": ..., ...}`. This Lambda owns all DynamoDB
access for the three AutoResearch tables and returns plain JSON (not API-Gateway-wrapped — it is
invoked directly, not through HTTP).

Actions:
  READ:
    get_project        {project_id}                         -> {project}
    list_experiments   {project_id}                          -> {experiments, count}
    get_guidance       {project_id}                          -> {guidance, count}   (unprocessed only)
  WRITE:
    create_experiment  {project_id, experiment_id, item}     -> {experiment_id}
    update_experiment  {project_id, experiment_id, updates}  -> {experiment_id, updated}
    update_project     {project_id, updates}                 -> {project_id, updated}
    mark_guidance_processed {project_id, guidance_id}         -> {guidance_id}

Every response is {"ok": true, ...} or {"ok": false, "error": "..."}.
"""

import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr

dynamodb = boto3.resource("dynamodb")

PROJECTS = os.environ["PROJECTS_TABLE"]
EXPERIMENTS = os.environ["EXPERIMENTS_TABLE"]
GUIDANCE = os.environ["GUIDANCE_TABLE"]

# Fields on the project record the agent is allowed to change (guards against a compromised agent
# rewriting arbitrary project state — e.g. it can't flip a foreign field or escalate).
PROJECT_WRITABLE = {"best_metric", "spent_usd", "researcher_cost_usd", "status", "paper_pdf_s3_uri"}

# `status` is the lifecycle ENUM the research loop keys on (the loop STOPS when status != "active").
# It must never hold free text: an agent that wrote a progress summary here would silently pause its
# own run. So writes to `status` are validated against this set — anything else is rejected. (Progress
# summaries belong in report/status.md, not this field.)
STATUS_VALUES = {"provisioning", "setup", "active", "paused", "completed", "provision_failed"}


def _to_dynamo(v):
    """JSON value -> DynamoDB-resource value (numbers must be Decimal)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, list):
        return [_to_dynamo(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_dynamo(x) for k, x in v.items()}
    return v


def _clean(v):
    """DynamoDB value -> JSON-serializable (Decimal -> int/float)."""
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, Decimal):
        return int(v) if v % 1 == 0 else float(v)
    return v


def _req(event, *keys):
    for k in keys:
        if not event.get(k):
            raise ValueError(f"missing required field: {k}")


def _apply_updates(table, key, updates):
    """SET each key=value with placeholders (safe for reserved words like status/state)."""
    if not updates:
        return
    names, values, sets = {}, {}, []
    for i, (k, val) in enumerate(updates.items()):
        nk, vk = f"#n{i}", f":v{i}"
        names[nk] = k
        values[vk] = _to_dynamo(val)
        sets.append(f"{nk} = {vk}")
    table.update_item(
        Key=key,
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


# --------------------------------------------------------------------------- read actions

def _check_token(event):
    """Bind the caller to the project it names (security-review #3). Each project has a random
    `agent_token` (in its record + a per-project SSM param only that project's role can read); the
    researcher sends it as `_token`. A compromised box can read only ITS OWN token, so it cannot
    forge calls for another project. Legacy projects without a token are allowed (backward compat)."""
    pid = event.get("project_id")
    if not pid:
        return
    item = dynamodb.Table(PROJECTS).get_item(
        Key={"project_id": pid}, ProjectionExpression="agent_token").get("Item") or {}
    expected = item.get("agent_token")
    if expected and event.get("_token") != expected:
        raise PermissionError("caller is not bound to this project_id")


def get_project(event):
    _req(event, "project_id")
    item = dynamodb.Table(PROJECTS).get_item(Key={"project_id": event["project_id"]}).get("Item")
    if item:
        item.pop("agent_token", None)   # never expose the capability token to instances
    return {"project": item}


def list_experiments(event):
    _req(event, "project_id")
    resp = dynamodb.Table(EXPERIMENTS).query(
        KeyConditionExpression=Key("project_id").eq(event["project_id"])
    )
    items = resp.get("Items", [])
    items.sort(key=lambda x: x.get("created_at", ""))
    return {"experiments": items, "count": len(items)}


def get_guidance(event):
    _req(event, "project_id")
    resp = dynamodb.Table(GUIDANCE).query(
        KeyConditionExpression=Key("project_id").eq(event["project_id"]),
        FilterExpression=Attr("processed").eq(False),
    )
    items = resp.get("Items", [])
    return {"guidance": items, "count": len(items)}


# --------------------------------------------------------------------------- write actions

def create_experiment(event):
    _req(event, "project_id", "experiment_id")
    item = _to_dynamo(event.get("item") or {})
    item["project_id"] = event["project_id"]
    item["experiment_id"] = event["experiment_id"]
    # Don't clobber an existing experiment with the same id.
    dynamodb.Table(EXPERIMENTS).put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(experiment_id)",
    )
    return {"experiment_id": event["experiment_id"]}


def update_experiment(event):
    _req(event, "project_id", "experiment_id")
    updates = event.get("updates") or {}
    _apply_updates(
        dynamodb.Table(EXPERIMENTS),
        {"project_id": event["project_id"], "experiment_id": event["experiment_id"]},
        updates,
    )
    return {"experiment_id": event["experiment_id"], "updated": sorted(updates.keys())}


def update_project(event):
    _req(event, "project_id")
    updates = event.get("updates") or {}
    bad = set(updates) - PROJECT_WRITABLE
    if bad:
        raise ValueError(f"fields not writable by the agent: {sorted(bad)}")
    # Guard the lifecycle enum: reject a free-text `status` (it would stop the loop). Progress notes
    # go in report/status.md, not here.
    if "status" in updates and updates["status"] not in STATUS_VALUES:
        raise ValueError(
            f"invalid status {updates['status']!r}; must be one of {sorted(STATUS_VALUES)}. "
            "Put progress summaries in report/status.md, not the status field."
        )
    _apply_updates(dynamodb.Table(PROJECTS), {"project_id": event["project_id"]}, updates)
    return {"project_id": event["project_id"], "updated": sorted(updates.keys())}


def mark_guidance_processed(event):
    _req(event, "project_id", "guidance_id")
    _apply_updates(
        dynamodb.Table(GUIDANCE),
        {"project_id": event["project_id"], "guidance_id": event["guidance_id"]},
        {"processed": True},
    )
    return {"guidance_id": event["guidance_id"]}


_ACTIONS = {
    "get_project": get_project,
    "list_experiments": list_experiments,
    "get_guidance": get_guidance,
    "create_experiment": create_experiment,
    "update_experiment": update_experiment,
    "update_project": update_project,
    "mark_guidance_processed": mark_guidance_processed,
}


def handler(event, context):
    event = event or {}
    action = event.get("action")
    fn = _ACTIONS.get(action)
    if fn is None:
        return {"ok": False, "error": f"unknown action: {action!r}", "valid_actions": sorted(_ACTIONS)}
    try:
        _check_token(event)   # caller→project binding before any read/write
        result = fn(event)
        out = {"ok": True}
        out.update(result)
        return _clean(out)
    except Exception as e:  # noqa: BLE001 — surface a clean error to the agent
        return {"ok": False, "error": str(e), "action": action}
