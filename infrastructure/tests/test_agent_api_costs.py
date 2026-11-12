"""agent_api cost metering — exercised against moto-mocked DynamoDB.

Run from infrastructure/:  python -m pytest -q tests
"""

import importlib
import os
import sys
import threading
import time
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "lambda_layers", "authz", "python"))
sys.path.insert(0, os.path.join(HERE, "..", "lambda", "agent_api"))

RESEARCHER, WORKER = "i-0researcher", "i-0worker"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("PROJECTS_TABLE", "P")
    monkeypatch.setenv("EXPERIMENTS_TABLE", "E")
    monkeypatch.setenv("GUIDANCE_TABLE", "G")
    monkeypatch.setenv("REGION", "eu-west-2")
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        ddb.create_table(TableName="P", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "project_id", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "project_id", "AttributeType": "S"}])
        ddb.create_table(TableName="E", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "project_id", "KeyType": "HASH"},
                                    {"AttributeName": "experiment_id", "KeyType": "RANGE"}],
                         AttributeDefinitions=[{"AttributeName": "project_id", "AttributeType": "S"},
                                               {"AttributeName": "experiment_id", "AttributeType": "S"}])
        ddb.create_table(TableName="G", BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "project_id", "KeyType": "HASH"},
                                    {"AttributeName": "guidance_id", "KeyType": "RANGE"}],
                         AttributeDefinitions=[{"AttributeName": "project_id", "AttributeType": "S"},
                                               {"AttributeName": "guidance_id", "AttributeType": "S"}])
        ddb.Table("P").put_item(Item={
            "project_id": "demo", "agent_token": "tok", "researcher_instance_id": RESEARCHER,
            "worker_instance_ids": [WORKER], "researcher_instance_type": "m8g.large",
            "worker_instance_type": "g4dn.xlarge", "researcher_hourly_usd": "0.1038",
            "worker_hourly_usd": "0.615", "spent_usd": "1.5", "researcher_cost_usd": "1.5",
            "budget_usd": "30"})
        import handler
        importlib.reload(handler)
        yield handler, ddb


def call(h, **event):
    event.setdefault("project_id", "demo")
    event.setdefault("_token", "tok")
    return h.handler(event, None)


def project(ddb):
    return ddb.Table("P").get_item(Key={"project_id": "demo"})["Item"]


def test_meter_instance_charges_region_rate_and_is_retry_safe(api):
    h, ddb = api
    t0 = int(time.time()) - 3600
    r = call(h, action="meter_instance", instance_id=WORKER, start_epoch=t0, end_epoch=t0 + 1800)
    assert r["ok"] and r["kind"] == "worker" and r["charged_seconds"] == 1800
    # retry of the same interval and an overlapping one: only the new 600 s are charged
    assert call(h, action="meter_instance", instance_id=WORKER, start_epoch=t0, end_epoch=t0 + 1800)["charged_usd"] == "0"
    r = call(h, action="meter_instance", instance_id=WORKER, start_epoch=t0 + 1000, end_epoch=t0 + 2400)
    assert r["charged_seconds"] == 600
    p = project(ddb)
    assert Decimal(p["worker_cost_usd"]) == Decimal("0.615") * 2400 / 3600
    # legacy spend is preserved and only ever increases
    assert Decimal(p["spent_usd"]) == Decimal("1.5") + Decimal(p["worker_cost_usd"])


def test_meter_instance_researcher_and_foreign_instance(api):
    h, ddb = api
    t0 = int(time.time()) - 7200
    r = call(h, action="meter_instance", instance_id=RESEARCHER, start_epoch=t0, end_epoch=t0 + 3600)
    assert r["kind"] == "researcher"
    assert Decimal(project(ddb)["researcher_cost_usd"]) == Decimal("1.5") + Decimal("0.1038")
    bad = call(h, action="meter_instance", instance_id="i-0other", start_epoch=t0, end_epoch=t0 + 60)
    assert not bad["ok"] and "does not belong" in bad["error"]
    future = call(h, action="meter_instance", instance_id=WORKER, start_epoch=t0, end_epoch=int(time.time()) + 3600)
    assert not future["ok"]


def test_meter_claude_cumulative_and_attribution(api):
    h, ddb = api
    ddb.Table("E").put_item(Item={"project_id": "demo", "experiment_id": "step-1-a", "state": "running"})
    call(h, action="meter_claude", invocation_id="exp-step-1-a", cumulative_usd="0.40", experiment_id="step-1-a")
    call(h, action="meter_claude", invocation_id="exp-step-1-a", cumulative_usd="0.40", experiment_id="step-1-a")  # retry
    call(h, action="meter_claude", invocation_id="exp-step-1-a", cumulative_usd="1.00", experiment_id="step-1-a")
    call(h, action="meter_claude", invocation_id="exp-step-1-a", cumulative_usd="0.90")   # stale: ignored
    call(h, action="meter_claude", invocation_id="step-1", cumulative_usd="2.00")         # researcher step
    p = project(ddb)
    assert Decimal(p["claude_cost_usd"]) == Decimal("3.00")
    assert Decimal(p["spent_usd"]) == Decimal("4.50")
    e = ddb.Table("E").get_item(Key={"project_id": "demo", "experiment_id": "step-1-a"})["Item"]
    assert e["agent_cost_usd"] == Decimal("1.00") and e["cost_usd"] == Decimal("1.00")


def test_meter_claude_charges_project_even_without_experiment_row(api):
    h, ddb = api
    r = call(h, action="meter_claude", invocation_id="exp-x", cumulative_usd="0.5", experiment_id="missing")
    assert r["ok"] and Decimal(project(ddb)["claude_cost_usd"]) == Decimal("0.5")


def test_record_worker_usage_per_run(api):
    h, ddb = api
    ddb.Table("E").put_item(Item={"project_id": "demo", "experiment_id": "e1", "state": "running"})
    r = call(h, action="record_worker_usage", experiment_id="e1", worker_uptime_seconds=3600, run_id="r1")
    assert r["recorded"] and Decimal(r["worker_cost_usd"]) == Decimal("0.615")
    # a retry of the same run is a no-op; a re-dispatch (new run) accumulates
    assert not call(h, action="record_worker_usage", experiment_id="e1", worker_uptime_seconds=3600, run_id="r1")["recorded"]
    assert call(h, action="record_worker_usage", experiment_id="e1", worker_uptime_seconds=1800, run_id="r2")["recorded"]
    e = ddb.Table("E").get_item(Key={"project_id": "demo", "experiment_id": "e1"})["Item"]
    assert e["worker_uptime_seconds"] == 5400 and e["cost_usd"] == Decimal("0.9225")
    # attribution only — project totals come from meter_instance
    assert project(ddb)["spent_usd"] == "1.5"


def test_fallback_rate_is_used_but_not_cached(api, monkeypatch):
    h, ddb = api
    ddb.Table("P").update_item(Key={"project_id": "demo"}, UpdateExpression="REMOVE worker_hourly_usd")
    monkeypatch.setattr(h, "hourly_rate", lambda itype, region: (0.526, "static-us-east-1"))
    t0 = int(time.time()) - 3600
    call(h, action="meter_instance", instance_id=WORKER, start_epoch=t0, end_epoch=t0 + 3600)
    p = project(ddb)
    assert Decimal(p["worker_cost_usd"]) == Decimal("0.526")
    assert "worker_hourly_usd" not in p          # retried against the Pricing API next time


def test_agent_cannot_write_spend_or_experiment_cost(api):
    h, ddb = api
    r = call(h, action="update_project", updates={"spent_usd": "0"})
    assert not r["ok"] and "not writable" in r["error"]
    r = call(h, action="update_project", updates={"researcher_cost_usd": "0"})
    assert not r["ok"]
    call(h, action="create_experiment", experiment_id="e1", item={"state": "running", "cost_usd": "0.01"})
    r = call(h, action="update_experiment", experiment_id="e1",
             updates={"state": "kept", "cost_usd": "0.0001", "worker_uptime_seconds": 1})
    assert r["ok"] and r["ignored"] == ["cost_usd", "worker_uptime_seconds"]
    e = ddb.Table("E").get_item(Key={"project_id": "demo", "experiment_id": "e1"})["Item"]
    assert e["state"] == "kept" and "cost_usd" not in e and "worker_uptime_seconds" not in e


def test_concurrent_meters_never_lose_an_increment(api):
    h, ddb = api
    errors = []

    def claude(i):
        try:
            for k in range(1, 6):
                r = call(h, action="meter_claude", invocation_id=f"inv-{i}", cumulative_usd=str(k * 0.1))
                assert r["ok"], r
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=claude, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    p = project(ddb)
    assert Decimal(p["claude_cost_usd"]) == Decimal("2.0")      # 4 invocations x $0.50
    assert Decimal(p["spent_usd"]) == Decimal("3.5")


def test_rate_resolved_and_cached_when_missing(api, monkeypatch):
    h, ddb = api
    ddb.Table("P").update_item(Key={"project_id": "demo"}, UpdateExpression="REMOVE worker_hourly_usd")
    monkeypatch.setattr(h, "hourly_rate", lambda itype, region: (0.615, f"pricing-api:{region}"))
    t0 = int(time.time()) - 3600
    call(h, action="meter_instance", instance_id=WORKER, start_epoch=t0, end_epoch=t0 + 3600)
    p = project(ddb)
    assert p["worker_hourly_usd"] == "0.615" and p["worker_rate_source"] == "pricing-api:eu-west-2"
    assert Decimal(p["worker_cost_usd"]) == Decimal("0.615")


def test_list_experiments_paginates_and_filters(api):
    h, ddb = api
    big = "x" * 300_000                      # ~4 items per 1 MB page
    with ddb.Table("E").batch_writer() as b:
        for i in range(10):
            b.put_item(Item={"project_id": "demo", "experiment_id": f"e{i:02d}", "created_at": f"{i:02d}",
                             "state": "kept" if i % 2 else "discarded", "blob": big})
    r = call(h, action="list_experiments")
    assert r["count"] == 10
    assert call(h, action="list_experiments", states=["kept"])["count"] == 5
