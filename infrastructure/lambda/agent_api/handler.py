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
  COST METERING (the ONLY way spend changes — see "Cost model" below):
    meter_instance     {project_id, instance_id, start_epoch, end_epoch}       -> {kind, charged_seconds, charged_usd}
    meter_claude       {project_id, invocation_id, cumulative_usd, experiment_id?} -> {charged_usd}
    record_worker_usage {project_id, experiment_id, worker_uptime_seconds}      -> {recorded, worker_cost_usd}

Every response is {"ok": true, ...} or {"ok": false, "error": "..."}.

Cost model
----------
Project spend = claude_cost_usd + researcher_cost_usd + worker_cost_usd (all USD strings), kept in
`spent_usd`. Totals are only ever INCREASED, by the metering actions, never written directly:
- meter_instance: each researcher/worker instance meters its own running time (instance-meter.sh,
  launched at boot, ticks every ~60s). The caller sends an absolute [start, end] interval; we charge
  only the part beyond the instance's high-water mark (`meter_hwm[instance_id]`), so retries,
  overlapping ticks or a duplicated meter can never double-charge. Rate = the project's cached
  region-correct on-demand rate (ar_pricing).
- meter_claude: the researcher sends the CUMULATIVE USD of one `claude -p` invocation; we charge the
  increase over the last value seen for that invocation_id (`claude_hwm`) — again retry-safe.
- record_worker_usage: per-experiment ATTRIBUTION of worker time (display only — the project total
  already comes from meter_instance). Recorded once per experiment.
Updates use optimistic concurrency on `cost_version`, so concurrent meters (researcher heartbeat,
N workers, N sub-agents) never lose an increment.
"""

import os
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr

from ar_pricing import hourly_rate

dynamodb = boto3.resource("dynamodb")
_ddb = dynamodb.meta.client

PROJECTS = os.environ["PROJECTS_TABLE"]
EXPERIMENTS = os.environ["EXPERIMENTS_TABLE"]
GUIDANCE = os.environ["GUIDANCE_TABLE"]
REGION = os.environ.get("REGION") or os.environ.get("AWS_REGION", "us-east-1")

# Fields on the project record the agent is allowed to change (guards against a compromised agent
# rewriting arbitrary project state — e.g. it can't flip a foreign field or escalate). Spend fields
# are deliberately NOT here: they change only through the metering actions (monotonic).
PROJECT_WRITABLE = {"best_metric", "status", "paper_pdf_s3_uri"}

# Experiment cost fields are owned by the metering actions; agent writes to them are dropped.
EXPERIMENT_COST_FIELDS = {"cost_usd", "agent_cost_usd", "worker_cost_usd", "worker_uptime_seconds",
                          "worker_usage_runs"}

COST_TOTALS = ("claude_cost_usd", "researcher_cost_usd", "worker_cost_usd")
_CLAUDE_HWM_KEEP = 64              # recent invocations remembered for retry de-duplication
_MAX_INTERVAL_S = 7 * 86400        # sanity cap on one metering interval
_MAX_INVOCATION_USD = 10000.0      # sanity cap on one claude invocation

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
    table = dynamodb.Table(EXPERIMENTS)
    kw = {"KeyConditionExpression": Key("project_id").eq(event["project_id"])}
    items = []
    while True:  # paginate — a single Query page is capped at 1 MB
        resp = table.query(**kw)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    states = event.get("states")
    if states:
        items = [i for i in items if i.get("state") in set(states)]
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
    raw = {k: v for k, v in (event.get("item") or {}).items() if k not in EXPERIMENT_COST_FIELDS}
    item = _to_dynamo(raw)
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
    raw = event.get("updates") or {}
    updates = {k: v for k, v in raw.items() if k not in EXPERIMENT_COST_FIELDS}
    _apply_updates(
        dynamodb.Table(EXPERIMENTS),
        {"project_id": event["project_id"], "experiment_id": event["experiment_id"]},
        updates,
    )
    out = {"experiment_id": event["experiment_id"], "updated": sorted(updates.keys())}
    ignored = sorted(set(raw) & EXPERIMENT_COST_FIELDS)
    if ignored:
        out["ignored"] = ignored   # cost fields are metered, not agent-written
    return out


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


# --------------------------------------------------------------------------- cost metering

def _usd(v) -> Decimal:
    try:
        d = Decimal(str(v))
        return d if d.is_finite() else Decimal(0)
    except Exception:  # noqa: BLE001
        return Decimal(0)


def _fmt(d: Decimal) -> str:
    return f"{d.quantize(Decimal('0.000001')):f}"


def _project_rate(p, kind):
    """(Decimal $/h, record-updates-to-cache-it) for kind in {researcher, worker}.

    Only an authoritative Pricing-API rate is cached on the record; a fallback rate is used for this
    call but not persisted, so the next call retries the Pricing API."""
    cached = p.get(f"{kind}_hourly_usd")
    if cached not in (None, "") and str(p.get(f"{kind}_rate_source", "pricing-api")).startswith("pricing-api"):
        rate = _usd(cached)
        if rate > 0:
            return rate, {}
    rate, src = hourly_rate(p.get(f"{kind}_instance_type") or "", REGION)
    if not src.startswith("pricing-api"):
        return _usd(rate), {}
    return _usd(rate), {f"{kind}_hourly_usd": str(rate), f"{kind}_rate_source": src}


def _charge(project_id, compute, experiment_update=None):
    """Optimistic-concurrency read→compute→conditional write of the project's cost totals.

    `compute(project) -> (deltas: {total_field: Decimal}, extra_sets: dict, result: dict)` or None
    (nothing to charge). `experiment_update(deltas)` may return a TransactWriteItems Update for the
    experiment row, written atomically with the project update.
    """
    pt = dynamodb.Table(PROJECTS)
    for _ in range(12):
        p = pt.get_item(Key={"project_id": project_id}, ConsistentRead=True).get("Item")
        if not p:
            raise ValueError(f"project not found: {project_id}")
        out = compute(p)
        if out is None:
            return {"charged_usd": "0"}
        deltas, extra_sets, result = out
        totals = {f: _usd(p.get(f, 0)) + deltas.get(f, Decimal(0)) for f in COST_TOTALS}
        spent = _usd(p.get("spent_usd", 0)) + sum(deltas.values(), Decimal(0))
        sets = {**{f: _fmt(v) for f, v in totals.items()}, "spent_usd": _fmt(spent), **extra_sets}
        version = p.get("cost_version")
        names = {f"#n{i}": k for i, k in enumerate(sets)}
        values = {f":v{i}": _to_dynamo(v) for i, v in enumerate(sets.values())}
        expr = "SET " + ", ".join(f"#n{i} = :v{i}" for i in range(len(sets))) + ", #cv = :newv"
        names["#cv"] = "cost_version"
        values[":newv"] = Decimal(int(version or 0) + 1)
        if version is None:
            cond = "attribute_not_exists(#cv)"
        else:
            cond = "#cv = :oldv"
            values[":oldv"] = version
        project_op = {"Update": {
            "TableName": PROJECTS, "Key": _ser_map({"project_id": project_id}),
            "UpdateExpression": expr, "ConditionExpression": cond,
            "ExpressionAttributeNames": names, "ExpressionAttributeValues": _ser_map(values)}}
        ops = [project_op]
        exp_op = experiment_update(deltas) if experiment_update else None
        if exp_op:
            ops.append(exp_op)
        try:
            _ddb.transact_write_items(TransactItems=ops)
            return {**result, "charged_usd": _fmt(sum(deltas.values(), Decimal(0))),
                    "spent_usd": sets["spent_usd"]}
        except _ddb.exceptions.TransactionCanceledException as e:
            reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
            if len(reasons) > 1 and reasons[1] == "ConditionalCheckFailed" and reasons[0] in (None, "None"):
                experiment_update = None   # experiment row vanished — still charge the project
            time.sleep(0.05)
            continue   # lost the race on cost_version → re-read and retry
    raise RuntimeError("cost update contention — retry")


def _ser_map(d):
    # `dynamodb.meta.client` (the resource's client) auto-serializes plain Python values, so items are
    # passed through unchanged — this only exists to keep the call sites explicit.
    return dict(d)


def meter_instance(event):
    """Charge an instance's running time for [start_epoch, end_epoch], beyond its high-water mark."""
    _req(event, "project_id", "instance_id")
    iid = event["instance_id"]
    start, end = int(event.get("start_epoch", 0)), int(event.get("end_epoch", 0))
    if start <= 0 or end < start or end - start > _MAX_INTERVAL_S or end > time.time() + 300:
        raise ValueError(f"invalid metering interval [{start}, {end}]")

    def compute(p):
        if iid == p.get("researcher_instance_id"):
            kind = "researcher"
        elif iid in (p.get("worker_instance_ids") or []):
            kind = "worker"
        else:
            raise ValueError(f"instance {iid} does not belong to project {p['project_id']}")
        hwm = dict(p.get("meter_hwm") or {})
        prev = int(hwm.get(iid, 0))
        secs = max(0, end - max(start, prev))
        if secs == 0:
            return None
        rate, rate_sets = _project_rate(p, kind)
        usd = rate * Decimal(secs) / Decimal(3600)
        hwm[iid] = max(prev, end)
        return ({f"{kind}_cost_usd": usd}, {"meter_hwm": hwm, **rate_sets},
                {"kind": kind, "charged_seconds": secs})

    return _charge(event["project_id"], compute)


def meter_claude(event):
    """Charge the increase in one claude invocation's cumulative USD (retry-safe)."""
    _req(event, "project_id", "invocation_id")
    inv = str(event["invocation_id"])[:128]
    cum = _usd(event.get("cumulative_usd", 0))
    if cum < 0 or cum > Decimal(str(_MAX_INVOCATION_USD)):
        raise ValueError(f"invalid cumulative_usd {cum}")
    exp_id = event.get("experiment_id")
    if exp_id:
        exp = dynamodb.Table(EXPERIMENTS).get_item(
            Key={"project_id": event["project_id"], "experiment_id": exp_id}).get("Item")
        # Attribute only to an existing row whose cost_usd is numeric (legacy rows stored a string,
        # which ADD cannot touch) — the project is charged regardless.
        if not exp or isinstance(exp.get("cost_usd"), str):
            exp_id = None

    def compute(p):
        hwm = dict(p.get("claude_hwm") or {})
        prev = _usd((hwm.get(inv) or {}).get("usd", 0))
        delta = cum - prev
        if delta <= 0:
            return None
        hwm[inv] = {"usd": _fmt(cum), "t": int(time.time())}
        if len(hwm) > _CLAUDE_HWM_KEEP:   # keep only the most recent invocations
            keep = sorted(hwm.items(), key=lambda kv: int(kv[1].get("t", 0)), reverse=True)[:_CLAUDE_HWM_KEEP]
            hwm = dict(keep)
        return {"claude_cost_usd": delta}, {"claude_hwm": hwm}, {}

    def exp_update(deltas):
        if not exp_id:
            return None
        d = deltas.get("claude_cost_usd", Decimal(0))
        return {"Update": {
            "TableName": EXPERIMENTS,
            "Key": _ser_map({"project_id": event["project_id"], "experiment_id": exp_id}),
            "UpdateExpression": "ADD agent_cost_usd :d, cost_usd :d",
            "ConditionExpression": "attribute_exists(experiment_id)",
            "ExpressionAttributeValues": _ser_map({":d": d})}}

    return _charge(event["project_id"], compute, exp_update)


def record_worker_usage(event):
    """Attribute metered worker running time to one experiment run. Display only: the project's
    worker_cost_usd already includes this time via meter_instance. Additive per run (`run_id`), so a
    re-dispatched experiment accumulates both runs, and a retried call is a no-op."""
    _req(event, "project_id", "experiment_id")
    secs = int(event.get("worker_uptime_seconds", 0))
    if secs < 0 or secs > _MAX_INTERVAL_S:
        raise ValueError(f"invalid worker_uptime_seconds {secs}")
    run_id = str(event.get("run_id") or "default")[:128]
    p = dynamodb.Table(PROJECTS).get_item(Key={"project_id": event["project_id"]}).get("Item") or {}
    rate, rate_sets = _project_rate(p, "worker")
    if rate_sets:
        _apply_updates(dynamodb.Table(PROJECTS), {"project_id": event["project_id"]}, rate_sets)
    cost = (rate * Decimal(secs) / Decimal(3600)).quantize(Decimal("0.000001"))
    try:
        dynamodb.Table(EXPERIMENTS).update_item(
            Key={"project_id": event["project_id"], "experiment_id": event["experiment_id"]},
            UpdateExpression="ADD worker_uptime_seconds :s, worker_cost_usd :c, cost_usd :c, worker_usage_runs :r",
            ConditionExpression="attribute_exists(experiment_id) AND NOT contains(worker_usage_runs, :rid)",
            ExpressionAttributeValues={":s": Decimal(secs), ":c": cost, ":r": {run_id}, ":rid": run_id},
        )
    except _ddb.exceptions.ConditionalCheckFailedException:
        return {"recorded": False, "reason": "experiment missing or this run already recorded"}
    except _ddb.exceptions.ClientError as e:  # e.g. a legacy row whose cost_usd is a string
        return {"recorded": False, "reason": str(e)[:200]}
    return {"recorded": True, "worker_uptime_seconds": secs, "worker_cost_usd": _fmt(cost)}


_ACTIONS = {
    "get_project": get_project,
    "list_experiments": list_experiments,
    "get_guidance": get_guidance,
    "create_experiment": create_experiment,
    "update_experiment": update_experiment,
    "update_project": update_project,
    "mark_guidance_processed": mark_guidance_processed,
    "meter_instance": meter_instance,
    "meter_claude": meter_claude,
    "record_worker_usage": record_worker_usage,
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
