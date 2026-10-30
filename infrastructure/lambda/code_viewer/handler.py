"""
code_viewer: Read-only CodeCommit views for the {project}-code repo.

Routes (dispatched by routeKey):
- GET /projects/{id}/code/tree?path=/        -> one folder level {entries:[{name,path,type}]}
- GET /projects/{id}/code/file?path=...       -> {path, content}
- GET /projects/{id}/code/commits?limit=30    -> {commits:[{id,message,author,date}]}
- GET /projects/{id}/code/diff?ref=&base=main -> {diffs:[{path,change_type,after_content}]}
"""

import difflib
import json
import os
from datetime import datetime, timezone

import boto3

codecommit = boto3.client("codecommit")
DEFAULT_BRANCH = "main"
REPO_SUFFIX = "-code"
MAX_DIFF_BYTES = 200 * 1024


def _iso_date(cc_date):
    """CodeCommit author.date is '<unix-seconds> <tz-offset>' (e.g. '1700000000 +0000').
    Return an ISO-8601 UTC string the browser can parse; '' if unparseable."""
    try:
        secs = int(str(cc_date).split()[0])
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, IndexError, TypeError):
        return ""


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def _repo(project_id):
    return f"{project_id}{REPO_SUFFIX}"


def _decode(raw):
    try:
        return raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)


def handler(event, context):
    project_id = event["pathParameters"]["projectId"]
    params = event.get("queryStringParameters") or {}
    route = event.get("routeKey", "")
    repo = _repo(project_id)

    try:
        if route.endswith("/code/tree"):
            return _tree(repo, params.get("path", "/"))
        if route.endswith("/code/file"):
            return _file(repo, params.get("path", ""))
        if route.endswith("/code/commits"):
            return _commits(repo, int(params.get("limit", 30)))
        if route.endswith("/code/diff"):
            # base="" (default) -> _diff uses the commit's own parent, so a commit shows the change
            # IT introduced (not a diff against main's HEAD, which would be empty for main commits).
            return _diff(repo, params.get("ref", ""), params.get("base", ""))
        return _resp(404, {"error": f"Unknown route {route}"})
    except codecommit.exceptions.RepositoryDoesNotExistException:
        return _resp(404, {"error": f"Repository {repo} not found"})
    except Exception as e:  # noqa: BLE001
        return _resp(500, {"error": str(e)})


def _tree(repo, path):
    folder = path or "/"
    resp = codecommit.get_folder(repositoryName=repo, folderPath=folder)
    entries = []
    for sf in resp.get("subFolders", []):
        entries.append({"name": sf["relativePath"], "path": sf["absolutePath"], "type": "dir"})
    for f in resp.get("files", []):
        entries.append({"name": f["relativePath"], "path": f["absolutePath"], "type": "file"})
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return _resp(200, {"path": folder, "entries": entries})


def _file(repo, path):
    if not path:
        return _resp(400, {"error": "Missing 'path'"})
    resp = codecommit.get_file(repositoryName=repo, filePath=path)
    return _resp(200, {"path": path, "content": _decode(resp["fileContent"])})


def _commits(repo, limit):
    branch = codecommit.get_branch(repositoryName=repo, branchName=DEFAULT_BRANCH)
    commit_id = branch["branch"]["commitId"]
    commits = []
    while commit_id and len(commits) < limit:
        c = codecommit.get_commit(repositoryName=repo, commitId=commit_id)["commit"]
        commits.append({
            # Full commit id — GetCommit / GetDifferences reject a truncated specifier. The UI
            # slices it to a short form for display and passes the full id back for the diff.
            "id": c["commitId"],
            "message": c.get("message", "").strip(),
            "author": c.get("author", {}).get("name", ""),
            "date": _iso_date(c.get("author", {}).get("date", "")),
        })
        parents = c.get("parents", [])
        commit_id = parents[0] if parents else None
    return _resp(200, {"commits": commits})


def _blob_text(repo, blob_id):
    """Decoded blob content, or '' if absent / too large to diff."""
    if not blob_id:
        return ""
    try:
        raw = codecommit.get_blob(repositoryName=repo, blobId=blob_id)["content"]
    except Exception:  # noqa: BLE001
        return ""
    if len(raw) > MAX_DIFF_BYTES:
        return "<file too large to diff>\n"
    return _decode(raw)


def _diff(repo, ref, base):
    """Unified diff for a commit. With no `base`, diff `ref` against its own first parent, so a
    single commit shows exactly the change it introduced (falls back to the empty tree for a root
    commit). Returns one unified-diff string per changed file."""
    if not ref:
        return _resp(400, {"error": "Missing 'ref'"})
    if not base:
        c = codecommit.get_commit(repositoryName=repo, commitId=ref)["commit"]
        parents = c.get("parents", [])
        base = parents[0] if parents else None

    kwargs = {"repositoryName": repo, "afterCommitSpecifier": ref}
    if base:
        kwargs["beforeCommitSpecifier"] = base
    diffs = []
    paginator = codecommit.get_paginator("get_differences")
    for page in paginator.paginate(**kwargs):
        for d in page.get("differences", []):
            after = d.get("afterBlob", {}) or {}
            before = d.get("beforeBlob", {}) or {}
            path = after.get("path") or before.get("path", "")
            before_lines = _blob_text(repo, before.get("blobId")).splitlines(keepends=True)
            after_lines = _blob_text(repo, after.get("blobId")).splitlines(keepends=True)
            udiff = "".join(
                difflib.unified_diff(
                    before_lines, after_lines, fromfile=f"a/{path}", tofile=f"b/{path}"
                )
            )
            diffs.append({"path": path, "change_type": d.get("changeType", ""), "diff": udiff})
    return _resp(200, {"ref": ref, "base": base, "diffs": diffs})
