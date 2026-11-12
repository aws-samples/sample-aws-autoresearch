import json

import pytest

from aws_auto_research.control_plane import Project
from aws_auto_research.researcher import dispatch


class FakeClient:
    def __init__(self, project):
        self.project = project
        self.claude = []
        self.worker_usage = []

    def get_project(self, pid):
        return Project.model_validate(self.project)

    def meter_claude(self, pid, inv, cum, exp_id=None):
        self.claude.append((inv, cum, exp_id)); return {"ok": True}

    def record_worker_usage(self, pid, exp_id, secs, run_id):
        self.worker_usage.append((exp_id, secs, run_id))
        return {"ok": True, "recorded": True, "worker_cost_usd": f"{secs / 3600 * 0.615:.6f}"}


class FakeRunner:
    def __init__(self):
        self.finalized = 0

    def finalize(self):
        self.finalized += 1


class FakeUsage:
    def __init__(self, secs):
        self.secs, self.state = secs, "stopped"

    def start(self):
        return self

    def wait_until_down(self, timeout_s=0):
        return True

    def stop(self):
        return self.secs


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PROJECT_ID", "demo")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.setenv("CLAUDE_MODEL", "eu.anthropic.claude-opus-4-8")
    monkeypatch.delenv("PAPER_RESERVE_USD", raising=False)
    monkeypatch.setattr(dispatch, "LogShipper",
                        lambda *a, **k: type("S", (), {"ship_line": lambda self, line: None})())
    monkeypatch.setattr(dispatch.subprocess, "run", lambda *a, **k: None)   # S3 archive
    runner = FakeRunner()
    monkeypatch.setattr(dispatch, "runner_for", lambda s, c: runner)
    return runner


def _project(**kw):
    p = {"project_id": "demo", "s3_data": "s3://b/demo", "experiment_runner": "ec2",
         "worker_instance_ids": ["i-0abc"], "budget_usd": "30", "spent_usd": "10"}
    p.update(kw)
    return p


def _fake_claude(lines, rc, result_json=None, argv_seen=None):
    """A run_metered stand-in that 'streams' lines through the real meter and optionally writes $RESULT_FILE."""
    def run(argv, env, meter, on_line):
        if argv_seen is not None:
            argv_seen.extend(argv)
        if result_json is not None:
            open(env["RESULT_FILE"], "w").write(json.dumps(result_json))
        meter.start_ticker()
        try:
            for ln in lines:
                on_line(ln.encode())
                meter.feed(ln)
        finally:
            meter.close()
        return rc
    return run


ASSISTANT = json.dumps({"type": "assistant", "message": {
    "id": "m1", "model": "claude-opus-4-8", "usage": {"input_tokens": 0, "output_tokens": 40000}}})


def test_timeout_without_result_is_still_fully_costed(env, monkeypatch):
    c = FakeClient(_project())
    monkeypatch.setattr(dispatch, "ControlPlaneClient", lambda region: c)
    monkeypatch.setattr(dispatch, "WorkerUsage", lambda describe: FakeUsage(900))
    monkeypatch.setattr(dispatch, "run_metered", _fake_claude([ASSISTANT], rc=124))

    r = dispatch.dispatch_experiment("step-1-a", worker_id="i-0abc")

    assert r["status"] == "timeout"
    agent = 40000 * 25 / 1e6 * 1.10                     # partial stream, no `result` event
    assert float(r["agent_cost_usd"]) == pytest.approx(agent, abs=1e-4)
    assert r["worker_uptime_seconds"] == 900
    assert float(r["worker_cost_usd"]) == pytest.approx(900 / 3600 * 0.615, abs=1e-4)
    assert c.claude[-1][0].startswith("exp-step-1-a-") and c.claude[-1][2] == "step-1-a"   # attributed
    assert [(e, s) for e, s, _ in c.worker_usage] == [("step-1-a", 900)]
    assert c.worker_usage[0][2] == c.claude[-1][0]       # same run id for both attributions
    assert env.finalized == 1                            # worker stop guaranteed


def test_redispatch_is_a_new_invocation(env, monkeypatch):
    c = FakeClient(_project())
    monkeypatch.setattr(dispatch, "ControlPlaneClient", lambda region: c)
    monkeypatch.setattr(dispatch, "WorkerUsage", lambda describe: FakeUsage(60))
    monkeypatch.setattr(dispatch, "run_metered", _fake_claude([ASSISTANT], rc=0))
    dispatch.dispatch_experiment("step-1-a", worker_id="i-0abc")
    dispatch.dispatch_experiment("step-1-a", worker_id="i-0abc")
    assert len({inv for inv, _, _ in c.claude}) == 2      # both runs charged in full
    assert len({r for _, _, r in c.worker_usage}) == 2


def test_worker_stop_verification_error_keeps_result(env, monkeypatch):
    c = FakeClient(_project())
    monkeypatch.setattr(dispatch, "ControlPlaneClient", lambda region: c)

    class Flaky(FakeUsage):
        def wait_until_down(self, timeout_s=0):
            raise ConnectionError("EC2 endpoint unreachable")
    monkeypatch.setattr(dispatch, "WorkerUsage", lambda describe: Flaky(120))
    ok = {"experiment_id": "step-1-a", "status": "keep", "metric_value": "0.9"}
    monkeypatch.setattr(dispatch, "run_metered", _fake_claude([], rc=0, result_json=ok))
    r = dispatch.dispatch_experiment("step-1-a", worker_id="i-0abc")
    assert r["metric_value"] == "0.9" and r["worker_uptime_seconds"] == 120


def test_agent_written_cost_is_overridden(env, monkeypatch):
    c = FakeClient(_project())
    monkeypatch.setattr(dispatch, "ControlPlaneClient", lambda region: c)
    monkeypatch.setattr(dispatch, "WorkerUsage", lambda describe: FakeUsage(230))
    lying = {"experiment_id": "step-1-a", "status": "keep", "metric_value": "0.9",
             "cost_usd": "0.0001", "worker_uptime_seconds": 1}
    monkeypatch.setattr(dispatch, "run_metered", _fake_claude([], rc=0, result_json=lying))

    r = dispatch.dispatch_experiment("step-1-a", worker_id="i-0abc")

    assert r["metric_value"] == "0.9" and r["status"] == "keep"
    assert r["worker_uptime_seconds"] == 230
    assert float(r["cost_usd"]) == pytest.approx(230 / 3600 * 0.615, abs=1e-4)


def test_subagent_budget_is_bounded_by_remaining(env, monkeypatch):
    c = FakeClient(_project(budget_usd="30", spent_usd="28.9"))   # $1.10 left
    monkeypatch.setattr(dispatch, "ControlPlaneClient", lambda region: c)
    monkeypatch.setattr(dispatch, "WorkerUsage", lambda describe: FakeUsage(0))
    argv = []
    monkeypatch.setattr(dispatch, "run_metered", _fake_claude([], rc=0, argv_seen=argv))

    dispatch.dispatch_experiment("step-1-a", worker_id="i-0abc", n_concurrent=2)

    cap = float(argv[argv.index("--max-budget-usd") + 1])
    assert cap == pytest.approx(1.10 / 2 / 1.10, abs=1e-3)       # half the remainder, in list $


def test_exhausted_budget_does_not_launch(env, monkeypatch):
    c = FakeClient(_project(budget_usd="30", spent_usd="29.99"))   # below the minimum invocation budget
    monkeypatch.setattr(dispatch, "ControlPlaneClient", lambda region: c)

    def never(*a, **k):
        raise AssertionError("must not launch a sub-agent")
    monkeypatch.setattr(dispatch, "run_metered", never)
    monkeypatch.setattr(dispatch, "WorkerUsage", never)

    r = dispatch.dispatch_experiment("step-1-a", worker_id="i-0abc")
    assert r["status"] == "crash" and "budget" in r["notes"]
