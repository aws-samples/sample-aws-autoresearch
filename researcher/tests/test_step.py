import json

import pytest

from aws_auto_research.config import Settings
from aws_auto_research.researcher import step


class FakeClient:
    def __init__(self, project=None, experiments=None, guidance=None):
        self._project = project or {"project_id": "demo", "experiment_runner": "local",
                                    "s3_data": "s3://b/demo", "best_metric": "0.9", "status": "active"}
        self._experiments = experiments or []
        self._guidance = guidance or []
        self.calls = []

    # only the attributes step.py touches
    def get_project(self, pid):
        from aws_auto_research.control_plane import Project
        return Project.model_validate(self._project)

    def update_project(self, pid, updates):
        self.calls.append(("update_project", updates)); return {"ok": True}

    def create_experiment(self, pid, eid, item):
        self.calls.append(("create_experiment", eid, item)); return {"ok": True}

    def update_experiment(self, pid, eid, updates):
        self.calls.append(("update_experiment", eid, updates)); return {"ok": True}

    def list_experiments(self, pid, states=None):
        self.calls.append(("list_experiments", states)); return self._experiments

    def get_guidance(self, pid):
        return self._guidance

    def mark_guidance_processed(self, pid, gid):
        self.calls.append(("mark_guidance_processed", gid)); return {"ok": True}


@pytest.fixture
def s(tmp_path):
    return Settings(region="us-east-1", project_id="demo", code_dir=str(tmp_path))


def test_experiment_item():
    it = step.experiment_item("step-1-a", "i-9", "hyp", "change", "NOW")
    assert it == {"state": "running", "hypothesis": "hyp", "description": "change",
                  "branch": "experiment/step-1-a", "worker_id": "i-9",
                  "log_stream": "experiment/step-1-a", "created_at": "NOW"}


def test_finalize_updates_drops_nulls():
    r = {"experiment_id": "e", "metric_value": "0.5", "peak_vram_mb": None, "cost_usd": "0.02",
         "run_seconds": 120, "log_stream": "experiment/e"}
    u = step.finalize_updates(r, "kept", "NOW")
    assert u["state"] == "kept" and u["metric_value"] == "0.5" and u["cost_usd"] == "0.02"
    assert u["completed_at"] == "NOW" and u["run_seconds"] == 120
    assert "peak_vram_mb" not in u and "agent_log_s3_uri" not in u   # nulls dropped


def test_update_project_status_guard(s):
    c = FakeClient()
    with pytest.raises(RuntimeError):
        step.update_project(c, s, "status", "in progress please wait")  # not an enum value
    step.update_project(c, s, "status", "paused")       # valid enum → allowed
    step.update_project(c, s, "best_metric", "0.1")     # non-status → allowed
    assert ("update_project", {"status": "paused"}) in c.calls
    assert ("update_project", {"best_metric": "0.1"}) in c.calls


def test_guidance(s):
    c = FakeClient(guidance=[{"id": "g1"}])
    step.guidance(c, s, "list")
    step.guidance(c, s, "done", "g1")
    assert ("mark_guidance_processed", "g1") in c.calls
    with pytest.raises(RuntimeError):
        step.guidance(c, s, "done")  # missing id


def test_list_and_pull_context(s, capsys):
    exps = [{"experiment_id": "step-1-a", "state": "kept", "metric_value": "0.9",
             "peak_vram_mb": "512", "description": "baseline"}]
    c = FakeClient(experiments=exps)
    step.pull_context(c, s)
    out = capsys.readouterr().out
    assert "EXPERIMENT_RUNNER=local" in out and "N_WORKERS=1" in out and "WORKERS=local" in out
    assert "BEST_METRIC=0.9" in out
    assert json.loads([ln for ln in out.splitlines() if ln.startswith("{")][0])["id"] == "step-1-a"
