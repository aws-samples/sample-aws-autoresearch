"""
data_viewer: Read-only S3 views for the project's data prefix.

All keys are under s3://{DATA_BUCKET}/{project}/. Routes (dispatched by routeKey):
- GET /projects/{id}/data/tree?prefix=       -> {entries:[{name,path,type,size}]}
- GET /projects/{id}/data/url?key=...         -> {url}  (presigned GET, 1h)
- GET /projects/{id}/data/preview?key=...     -> {content, truncated}  (text, whole lines)
"""

import json
import os
import boto3

s3 = boto3.client("s3")
DATA_BUCKET = os.environ["DATA_BUCKET"]
# Generous cap so agent logs (jsonl, often a few hundred KB) render fully.
PREVIEW_BYTES = 2 * 1024 * 1024


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


class _Traversal(Exception):
    """Raised when a caller-supplied key/prefix escapes the project prefix."""


def _contain(base, rel):
    """Resolve `base + rel` and assert it stays within `base`. Rejects `..` and absolute escapes so a
    caller cannot read another project's data or internal secrets (_provisioning/_platform/...)."""
    rel = rel or ""
    if rel.startswith("/") or ".." in rel.split("/"):
        raise _Traversal()
    key = os.path.normpath(base + rel)
    # normpath("proj/") drops the trailing slash; compare against the normalized base directory.
    if key != base.rstrip("/") and not key.startswith(base):
        raise _Traversal()
    return key


def handler(event, context):
    project_id = event["pathParameters"]["projectId"]
    params = event.get("queryStringParameters") or {}
    route = event.get("routeKey", "")
    base = f"{project_id}/"

    try:
        if route.endswith("/data/tree"):
            return _tree(base, params.get("prefix", ""))
        if route.endswith("/data/url"):
            return _url(base, params.get("key", ""))
        if route.endswith("/data/preview"):
            return _preview(base, params.get("key", ""))
        return _resp(404, {"error": f"Unknown route {route}"})
    except _Traversal:
        return _resp(400, {"error": "Invalid key"})
    except Exception as e:  # noqa: BLE001
        print(f"data_viewer error on {route} for {project_id}: {e}")  # detail server-side only (#12)
        return _resp(500, {"error": "Internal error"})


def _tree(base, prefix):
    _contain(base, prefix)   # reject traversal in the listing prefix
    full_prefix = base + (prefix or "")
    resp = s3.list_objects_v2(Bucket=DATA_BUCKET, Prefix=full_prefix, Delimiter="/")
    entries = []
    for cp in resp.get("CommonPrefixes", []):
        p = cp["Prefix"][len(base):]  # strip project prefix -> relative
        name = p.rstrip("/").split("/")[-1]
        entries.append({"name": name, "path": p, "type": "dir", "size": 0})
    for obj in resp.get("Contents", []):
        rel = obj["Key"][len(base):]
        if not rel or rel.endswith("/"):
            continue
        entries.append({"name": rel.split("/")[-1], "path": rel, "type": "file", "size": obj["Size"]})
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return _resp(200, {"prefix": prefix, "entries": entries})


def _url(base, key):
    if not key:
        return _resp(400, {"error": "Missing 'key'"})
    full = _contain(base, key)
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": DATA_BUCKET, "Key": full}, ExpiresIn=3600
    )
    return _resp(200, {"key": key, "url": url})


def _preview(base, key):
    if not key:
        return _resp(400, {"error": "Missing 'key'"})
    full = _contain(base, key)
    obj = s3.get_object(Bucket=DATA_BUCKET, Key=full, Range=f"bytes=0-{PREVIEW_BYTES - 1}")
    raw = obj["Body"].read()
    truncated = len(raw) >= PREVIEW_BYTES
    content = raw.decode("utf-8", errors="replace")
    # If we hit the cap, drop the trailing partial line so line-oriented viewers (jsonl logs)
    # never see a half-serialized object (which would fail to parse and render as raw).
    if truncated:
        nl = content.rfind("\n")
        if nl != -1:
            content = content[: nl + 1]
    return _resp(200, {"key": key, "content": content, "truncated": truncated})
