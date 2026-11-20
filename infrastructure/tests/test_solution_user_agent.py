"""Solution usage-metrics tagging for the Lambda layer hook and the CDK app."""

import importlib
import os
import sys

import boto3
import botocore.session

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "lambda_layers", "authz", "python"))
sys.path.insert(0, os.path.join(HERE, ".."))

import solution  # noqa: E402


class _Stop(Exception):
    pass


def test_lambda_layer_hook_tags_clients(monkeypatch):
    monkeypatch.setenv("USER_AGENT_STRING", solution.USER_AGENT)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    orig = botocore.session.Session.__init__
    import solution_user_agent as hook
    try:
        importlib.reload(hook)
        s3 = boto3.client("s3", region_name="us-east-1")
        captured = {}

        def grab(request, **kwargs):
            captured["ua"] = request.headers["User-Agent"].decode()
            raise _Stop()

        s3.meta.events.register("before-send.s3.*", grab)
        try:
            s3.list_buckets()
        except _Stop:
            pass
        assert captured["ua"].split().count(solution.USER_AGENT) == 1
    finally:
        botocore.session.Session.__init__ = orig


def test_descriptions():
    assert solution.USER_AGENT == "AWSSOLUTION/SO0382/v0.1.0"
    assert solution.description().startswith("(SO0382) ")
    assert solution.description("D", "Data Stack").startswith("(SO0382D) ")
