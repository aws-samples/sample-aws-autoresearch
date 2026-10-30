import json

import pytest

from aws_auto_research import aws
from aws_auto_research.experiment_runners.ec2 import EC2Runner
from aws_auto_research.experiment_runners.local import LocalRunner
from conftest import FakeBackend, build


# ---- shared steps (backend-agnostic) -----------------------------------------

def test_sync_code_issues_checkout(settings, project):
    be = FakeBackend()
    r = build(LocalRunner, settings, project, be)
    r.sync_code()
    assert any("git checkout -f -B" in c and settings.branch in c for c in be.commands)


def test_run_eval_reads_metrics(settings, project, capsys):
    be = FakeBackend(outputs={"metrics.json": '{"metric": 0.87, "peak_vram_mb": 512}'})
    r = build(LocalRunner, settings, project, be)
    m = r.run_eval()
    assert m is not None and m.metric == 0.87
    # stale-clear happened before the eval
    assert any(c.startswith("rm -f metrics.json") for c in be.commands)
    out = capsys.readouterr().out
    assert "metric: 0.87" in out and "peak_vram_mb: 512" in out


def test_run_eval_missing_metrics_is_crash(settings, project, capsys):
    be = FakeBackend()  # no metrics.json output
    r = build(LocalRunner, settings, project, be)
    assert r.run_eval() is None
    assert "ERROR" in capsys.readouterr().out


def test_push_and_collect_commands(settings, project):
    be = FakeBackend()
    r = build(LocalRunner, settings, project, be)
    r.push_branch()
    r.collect_artifacts("/tmp/ckpt")
    cmds = "\n".join(be.commands)
    assert f"git push origin {settings.branch}" in cmds
    assert "aws s3 cp run.log s3://bucket/demo/experiments/step-1-baseline/run.log" in cmds
    assert "aws s3 cp benchmarks/ s3://bucket/demo/benchmarks/step-1-baseline/" in cmds
    assert "aws s3 cp '/tmp/ckpt' s3://bucket/demo/checkpoints/step-1-baseline/" in cmds


# ---- cost differs by runner --------------------------------------------------

def test_local_cost_is_zero(settings, project):
    r = build(LocalRunner, settings, project, FakeBackend())
    r.write_result("keep", "0.5", "null", 300, uptime_seconds=999, notes="x")
    res = json.loads(open(settings.result_file).read())
    assert res["cost_usd"] == "0.0000" and res["worker_uptime_seconds"] == 0
    assert res["metric_value"] == "0.5" and res["peak_vram_mb"] is None


def test_ec2_cost_from_worker_uptime(settings, project):
    r = build(EC2Runner, settings, project, FakeBackend())
    r.write_result("keep", "0.5", "1240", 300, uptime_seconds=3600, notes="x")
    res = json.loads(open(settings.result_file).read())
    assert res["cost_usd"] == "0.5260"          # 1h @ g4dn.xlarge
    assert res["worker_uptime_seconds"] == 3600


# ---- ec2 prepare state machine (mocked AWS, no real waits) -------------------

def test_ec2_prepare_ready(settings, project, mocker):
    mocker.patch.object(aws, "instance_state", return_value="running")
    mocker.patch.object(aws, "wait_state", return_value=True)
    be = FakeBackend(outputs={".worker-bootstrap-complete": ""})  # test -f -> ok (exit 0)
    r = build(EC2Runner, settings, project, be)
    r.prepare(sleep=lambda _s: None)  # marker present immediately
    assert any("test -f ~/.worker-bootstrap-complete" in c for c in be.commands)


def test_ec2_prepare_starts_when_stopped(settings, project, mocker):
    # prepare checks state twice before starting (initial + the "!= running" gate), then again in
    # the readiness loop → stopped, stopped, running.
    states = iter(["stopped", "stopped", "running"])
    mocker.patch.object(aws, "instance_state", side_effect=lambda *a, **k: next(states, "running"))
    mocker.patch.object(aws, "wait_state", return_value=True)
    start = mocker.patch.object(aws, "start_instance")
    be = FakeBackend(outputs={".worker-bootstrap-complete": ""})
    r = build(EC2Runner, settings, project, be)
    r.prepare(sleep=lambda _s: None)
    start.assert_called_once()


def test_ec2_prepare_capacity_error(settings, project, mocker):
    mocker.patch.object(aws, "instance_state", return_value="stopped")
    mocker.patch.object(aws, "wait_state", return_value=True)
    r = build(EC2Runner, settings, project, FakeBackend())
    mocker.patch.object(r, "_start", side_effect=aws.CapacityError("no capacity"))
    with pytest.raises(aws.CapacityError):
        r.prepare(sleep=lambda _s: None)
