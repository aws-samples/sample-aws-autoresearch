"""create_project: presigned large-upload path + batched CodeCommit commits."""

import importlib.util
import boto3
import json
import os
import sys
from unittest import mock

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "lambda_layers", "authz", "python"))
sys.path.insert(0, os.path.join(HERE, ".."))

for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t",
             "PROJECTS_TABLE": "p", "DATA_BUCKET": "bkt", "INSTANCE_PROFILE_NAME": "ip",
             "RESEARCHER_SECURITY_GROUP_ID": "sg", "WORKER_SECURITY_GROUP_ID": "sg", "WORKER_KEY_NAME": "k",
             "REGION": "us-east-1", "ACCOUNT_ID": "1"}.items():
    os.environ.setdefault(k, v)


def _load():
    path = os.path.join(HERE, "..", "lambda", "create_project", "handler.py")
    spec = importlib.util.spec_from_file_location("create_project_handler", path)
    mod = importlib.util.module_from_spec(spec)
    # The handler imports the SDK user-agent hook, which patches botocore globally; undo that so
    # it doesn't leak into test_solution_user_agent.
    import botocore.session
    orig = botocore.session.Session.__init__
    try:
        spec.loader.exec_module(mod)
    finally:
        botocore.session.Session.__init__ = orig
        boto3.DEFAULT_SESSION = None  # drop the session built without the hook
    return mod


def _event(path, sub="user-1", body=None):
    return {"rawPath": path, "body": json.dumps(body or {}),
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": sub, "username": "u"}}}}}


def test_upload_url_is_scoped_to_caller():
    h = _load()
    with mock.patch.object(h, "s3") as s3:
        s3.generate_presigned_url.return_value = "https://signed"
        r = h.handler(_event("/projects/upload-url"), None)
    body = json.loads(r["body"])
    assert r["statusCode"] == 200 and body["url"] == "https://signed"
    key = s3.generate_presigned_url.call_args.kwargs["Params"]["Key"]
    assert key == f"_uploads/user-1/{body['upload_id']}.json"


def test_upload_url_requires_identity():
    h = _load()
    assert h.handler(_event("/projects/upload-url", sub=""), None)["statusCode"] == 403


def test_create_rejects_bad_upload_id():
    h = _load()
    r = h.handler(_event("/projects", body={"upload_id": "../../etc"}), None)
    assert r["statusCode"] == 400


def test_commit_is_split_into_batches():
    h = _load()
    big = b"x" * (3 * 1024 * 1024)
    uploaded = {"src/a.bin": big, "src/b.bin": big, "src/c.bin": big, "auto-research.json": b"{}"}
    with mock.patch.object(h, "codecommit") as cc:
        cc.exceptions.ParentCommitIdRequiredException = type("PC", (Exception,), {})
        cc.create_commit.side_effect = [{"commitId": f"c{i}"} for i in range(10)]
        h._commit_code_repo("repo", uploaded, {}, [], "pid", "readme")
    calls = cc.create_commit.call_args_list
    assert len(calls) >= 3
    assert "parentCommitId" not in calls[0].kwargs
    assert calls[1].kwargs["parentCommitId"] == "c0"
    sizes = [sum(len(f["fileContent"]) for f in c.kwargs["putFiles"]) for c in calls]
    assert all(s <= 4 * 1024 * 1024 + 64 * 1024 for s in sizes)
    paths = {f["filePath"] for c in calls for f in c.kwargs["putFiles"]}
    assert {"src/a.bin", "src/b.bin", "src/c.bin", "auto-research.json", "CLAUDE.md"} <= paths
