"""
wiki_viewer: Read-only CodeCommit views for the {project}-wiki repo.

Routes (dispatched by routeKey):
- GET /projects/{id}/wiki/tree?path=/   -> one folder level {entries:[{name,path,type}]}
- GET /projects/{id}/wiki/file?path=... -> {path, content}
"""

import json
import os
import boto3

codecommit = boto3.client("codecommit")
REPO_SUFFIX = "-wiki"


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def _decode(raw):
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def handler(event, context):
    project_id = event["pathParameters"]["projectId"]
    params = event.get("queryStringParameters") or {}
    route = event.get("routeKey", "")
    repo = f"{project_id}{REPO_SUFFIX}"

    try:
        if route.endswith("/wiki/tree"):
            folder = params.get("path", "/") or "/"
            resp = codecommit.get_folder(repositoryName=repo, folderPath=folder)
            entries = [{"name": sf["relativePath"], "path": sf["absolutePath"], "type": "dir"}
                       for sf in resp.get("subFolders", [])]
            entries += [{"name": f["relativePath"], "path": f["absolutePath"], "type": "file"}
                        for f in resp.get("files", [])]
            entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
            return _resp(200, {"path": folder, "entries": entries})

        if route.endswith("/wiki/file"):
            path = params.get("path", "")
            if not path:
                return _resp(400, {"error": "Missing 'path'"})
            resp = codecommit.get_file(repositoryName=repo, filePath=path)
            return _resp(200, {"path": path, "content": _decode(resp["fileContent"])})

        return _resp(404, {"error": f"Unknown route {route}"})
    except codecommit.exceptions.RepositoryDoesNotExistException:
        return _resp(404, {"error": f"Repository {repo} not found"})
    except Exception as e:  # noqa: BLE001
        return _resp(500, {"error": str(e)})
