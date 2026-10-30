"""
get_logs: Tail the project CloudWatch log group for the web app's real-time streams.

Log group: /auto-research/{project}. Streams (each separately addressable):
- researcher-{instance_id}  -> the full AI Researcher log (a project links here)
- experiment/{experiment_id} -> one run-experiment sub-agent's log (each experiment links here)
- worker-{instance_id}      -> worker infra/bootstrap messages

If ?stream= is given, returns that stream; otherwise interleaves all streams (FilterLogEvents).
For smooth live tailing the response includes `next_start` (max event ts + 1); the UI polls again
with ?start=next_start to fetch only newer events. The complete/stored log is retrieved separately
via the S3 archive (GET /data/url?key=logs/researcher.jsonl or experiments/{id}/agent.jsonl).

Query params: stream (optional), limit (default 200, max 1000), start (epoch ms, optional).
"""

import json
import os
import boto3

logs = boto3.client("logs")


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def handler(event, context):
    project_id = event["pathParameters"]["projectId"]
    params = event.get("queryStringParameters") or {}
    stream = params.get("stream")
    limit = min(int(params.get("limit", 200)), 1000)
    start = params.get("start")

    log_group = f"/auto-research/{project_id}"
    events = []

    try:
        if stream:
            kwargs = {"logGroupName": log_group, "logStreamName": stream,
                      "limit": limit, "startFromHead": False}
            if start:
                kwargs["startTime"] = int(start)
            resp = logs.get_log_events(**kwargs)
            events = [{"timestamp": e["timestamp"], "message": e["message"], "stream": stream}
                      for e in resp.get("events", [])]
        else:
            kwargs = {"logGroupName": log_group, "limit": limit, "interleaved": True}
            if start:
                kwargs["startTime"] = int(start)
            resp = logs.filter_log_events(**kwargs)
            events = [{"timestamp": e["timestamp"], "message": e["message"],
                       "stream": e.get("logStreamName", "")}
                      for e in resp.get("events", [])]
    except logs.exceptions.ResourceNotFoundException:
        # Group/stream not created yet (project never started, or experiment not begun) —
        # return empty with a stable cursor, not an error.
        return _resp(200, {"project_id": project_id, "events": [], "count": 0,
                           "next_start": int(start) if start else 0})

    events.sort(key=lambda e: e["timestamp"])
    # Forward cursor for the UI to poll only newer events next time.
    next_start = (events[-1]["timestamp"] + 1) if events else (int(start) if start else 0)
    return _resp(200, {"project_id": project_id, "events": events,
                       "count": len(events), "next_start": next_start})
