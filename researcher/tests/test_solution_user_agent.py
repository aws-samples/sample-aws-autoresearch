"""Solution usage-metrics tagging: every boto3 client carries AWSSOLUTION/SO0382/<ver> as its own token."""

import importlib

import boto3
import botocore.session

SOLUTION_UA = "AWSSOLUTION/SO0382/v0.1.0"


class _Stop(Exception):
    pass


def _captured_user_agent(client, service, call):
    captured = {}

    def grab(request, **kwargs):
        captured["ua"] = request.headers["User-Agent"].decode()
        raise _Stop()

    client.meta.events.register(f"before-send.{service}.*", grab)
    try:
        call()
    except _Stop:
        pass
    finally:
        client.meta.events.unregister(f"before-send.{service}.*", grab)
    return captured["ua"]


def test_clients_carry_solution_user_agent(monkeypatch):
    monkeypatch.setenv("USER_AGENT_STRING", SOLUTION_UA)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    orig = botocore.session.Session.__init__
    import aws_auto_research.solution_user_agent as hook
    try:
        importlib.reload(hook)
        from aws_auto_research.aws import ec2

        client = ec2("us-east-1")
        for _ in range(2):  # repeat: token must appear exactly once
            ua = _captured_user_agent(client, "ec2", lambda: client.describe_instances()).split()
            assert ua.count(SOLUTION_UA) == 1
        s3 = boto3.client("s3", region_name="us-east-1")
        assert SOLUTION_UA in _captured_user_agent(s3, "s3", lambda: s3.list_buckets()).split()
    finally:
        botocore.session.Session.__init__ = orig
