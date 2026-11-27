"""Domain extensions: the cognito-user provider, extension cost metering and the budget hard stop (moto).

Run from infrastructure/:  python -m pytest -q tests
"""

import importlib
import json
import os
import sys
import time
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "lambda_layers", "authz", "python"))
sys.path.insert(0, os.path.join(HERE, "..", "lambda", "agent_api"))

import ar_extensions  # noqa: E402

REGION = "eu-west-2"


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_DEFAULT_REGION": REGION, "AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t",
                 "PROJECTS_TABLE": "P", "EXPERIMENTS_TABLE": "E", "GUIDANCE_TABLE": "G", "REGION": REGION,
                 "DATA_BUCKET": "bkt"}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        idp = boto3.client("cognito-idp", region_name=REGION)
        pool = idp.create_user_pool(PoolName="robots-sim")["UserPool"]["Id"]
        client = idp.create_user_pool_client(UserPoolId=pool, ClientName="web",
                                             ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"])
        idp.create_group(UserPoolId=pool, GroupName="automation")
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket="bkt", CreateBucketConfiguration={"LocationConstraint": REGION})
        doc = {"name": "robots-sim", "display_name": "robots-sim", "description": "sim", "web_url": "https://w",
               "provider": {"type": "cognito-user", "user_pool_id": pool,
                            "client_id": client["UserPoolClient"]["ClientId"], "region": REGION,
                            "groups": ["automation"]},
               "cost": {"type": "poll", "command": "rs costs --json"},
               "hooks": {"on_budget_exhausted": "disable-user", "on_delete": "delete-user"}}
        s3.put_object(Bucket="bkt", Key=ar_extensions.manifest_key("robots-sim"), Body=json.dumps(doc).encode())
        yield {"idp": idp, "pool": pool, "doc": doc, "s3": s3, "ssm": boto3.client("ssm", region_name=REGION)}


def test_manifests_load_and_list(aws):
    assert ar_extensions.load_manifest(aws["s3"], "bkt", "robots-sim")["provider"]["type"] == "cognito-user"
    with pytest.raises(ar_extensions.ExtensionError, match="not installed"):
        ar_extensions.load_manifest(aws["s3"], "bkt", "nope")
    with pytest.raises(ar_extensions.ExtensionError, match="invalid"):
        ar_extensions.load_manifest(aws["s3"], "bkt", "../x")
    [m] = ar_extensions.list_installed(aws["s3"], "bkt")
    assert m["name"] == "robots-sim" and m["metered"] and "user_pool_id" not in json.dumps(m) and m["installed_at"]
    assert ar_extensions.required_by({"extensions": ["robots-sim", "robots-sim"]}) == ["robots-sim"]
    with pytest.raises(ar_extensions.ExtensionError):
        ar_extensions.required_by({"extensions": "robots-sim"})


def test_a_project_gets_its_own_user_and_loses_it(aws):
    rec = ar_extensions.provision(boto3.session.Session(), "pickorange", aws["doc"], ssm=aws["ssm"])
    assert rec["status"] == "ok" and rec["identity"] == "ar-pickorange" and rec["provider"] == "cognito-user"
    assert "password" not in json.dumps({k: v for k, v in rec.items() if k != "password_param"})
    pw = aws["ssm"].get_parameter(Name=rec["password_param"], WithDecryption=True)["Parameter"]["Value"]
    assert rec["password_param"] == "/auto-research/projects/pickorange/ext/robots-sim/password" and len(pw) == 32
    groups = aws["idp"].admin_list_groups_for_user(UserPoolId=aws["pool"], Username="ar-pickorange")["Groups"]
    assert [g["GroupName"] for g in groups] == ["automation"]
    # re-provisioning the same project resets the password and keeps one user
    rec2 = ar_extensions.provision(boto3.session.Session(), "pickorange", aws["doc"], ssm=aws["ssm"])
    assert rec2["status"] == "ok"
    ar_extensions.run_builtin(boto3.session.Session(), "disable-user", aws["doc"], rec)
    assert not aws["idp"].admin_get_user(UserPoolId=aws["pool"], Username="ar-pickorange")["Enabled"]
    assert ar_extensions.run_builtin(boto3.session.Session(), "delete-user", aws["doc"], rec) == "delete-user ar-pickorange"
    assert ar_extensions.run_builtin(boto3.session.Session(), "delete-user", aws["doc"], rec) == "user already gone"
    with pytest.raises(ar_extensions.ExtensionError):
        ar_extensions.run_builtin(boto3.session.Session(), "rm -rf /", aws["doc"], rec)


@pytest.fixture
def api(aws):
    ddb = boto3.resource("dynamodb", region_name=REGION)
    for name, keys in (("P", ["project_id"]), ("E", ["project_id", "experiment_id"]), ("G", ["project_id", "guidance_id"])):
        ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": k, "KeyType": t} for k, t in zip(keys, ("HASH", "RANGE"))],
                         AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in keys])
    rec = ar_extensions.provision(boto3.session.Session(), "demo", aws["doc"], ssm=aws["ssm"])
    ddb.Table("P").put_item(Item={
        "project_id": "demo", "agent_token": "tok", "researcher_instance_id": "i-r", "worker_instance_ids": [],
        "researcher_instance_type": "m8g.large", "researcher_hourly_usd": "0.1038", "spent_usd": "1.0",
        "claude_cost_usd": "1.0", "budget_usd": "10", "extensions": {"robots-sim": rec}})
    import handler
    importlib.reload(handler)
    yield handler, ddb, aws


def call(h, **event):
    event.setdefault("project_id", "demo")
    event.setdefault("_token", "tok")
    return h.handler(event, None)


def project(ddb):
    return ddb.Table("P").get_item(Key={"project_id": "demo"})["Item"]


def test_extension_costs_count_running_calls_and_settle(api):
    h, ddb, _ = api
    r = call(h, action="meter_extension", extension="robots-sim", final_usd="0.50", active_usd="2.00")
    assert r["ok"] and r["extension_usd"] == "2.500000"
    p = project(ddb)
    assert Decimal(p["extension_cost_usd"]) == Decimal("2.5") and Decimal(p["spent_usd"]) == Decimal("3.5")
    # the running call finished cheaper than its estimate: the reservation settles down
    call(h, action="meter_extension", extension="robots-sim", final_usd="1.20", active_usd="0")
    assert Decimal(project(ddb)["spent_usd"]) == Decimal("2.2")
    # a smaller finished total (a stale or tampered report) never lowers what is already settled
    call(h, action="meter_extension", extension="robots-sim", final_usd="0.10", active_usd="0")
    assert Decimal(project(ddb)["extension_cost_usd"]) == Decimal("1.2")
    assert call(h, action="meter_extension", extension="robots-sim", final_usd="1.20", active_usd="0")["charged_usd"] == "0"
    assert not call(h, action="meter_extension", extension="other", final_usd="1")["ok"]
    assert not call(h, action="meter_extension", extension="robots-sim", final_usd="-1")["ok"]
    # the agent cannot write the totals directly
    assert not call(h, action="update_project", updates={"extension_cost_usd": "0"})["ok"]


def test_reaching_the_budget_disables_the_extensions_credentials(api):
    h, ddb, aws = api
    r = call(h, action="meter_extension", extension="robots-sim", final_usd="4", active_usd="2")
    assert r["ok"] and "budget_hard_stop" not in r
    assert aws["idp"].admin_get_user(UserPoolId=aws["pool"], Username="ar-demo")["Enabled"]
    t0 = int(time.time()) - 600
    r = call(h, action="meter_claude", invocation_id="step-1", cumulative_usd="3.5")    # 1 + 6 + 3.5 >= 10
    assert r["ok"] and r["budget_hard_stop"] == ["robots-sim: disable-user ar-demo"]
    assert not aws["idp"].admin_get_user(UserPoolId=aws["pool"], Username="ar-demo")["Enabled"]
    ext = project(ddb)["extensions"]["robots-sim"]
    assert ext["status"] == "disabled" and "budget reached" in ext["status_reason"]
    # once disabled, later meters do not run the hook again
    r = call(h, action="meter_instance", instance_id="i-r", start_epoch=t0, end_epoch=t0 + 60)
    assert r["ok"] and "budget_hard_stop" not in r
