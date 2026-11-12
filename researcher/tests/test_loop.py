import json

from aws_auto_research.control_plane import Project
from aws_auto_research.researcher import loop


def _p(**kw):
    return Project.model_validate({"project_id": "demo", **kw})


def test_paper_reserve_is_capped_for_small_budgets():
    env = {"PAPER_BUDGET_USD": "6.00"}
    assert loop._paper_reserve(env, _p(write_paper=False, budget_usd="5")) == 0.0
    assert loop._paper_reserve(env, _p(write_paper=True, budget_usd="5")) == 1.0     # 20% of $5
    assert loop._paper_reserve(env, _p(write_paper=True, budget_usd="100")) == 6.0
    assert loop._paper_reserve(env, _p(write_paper=True, budget_usd="0")) == 6.0     # no cap


def test_step_cost_record(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(loop, "_sh", lambda argv, **kw: calls.append(argv))
    before = _p(claude_cost_usd="1", researcher_cost_usd="0.1", worker_cost_usd="0.2", spent_usd="1.3")
    after = _p(claude_cost_usd="2.5", researcher_cost_usd="0.15", worker_cost_usd="0.4", spent_usd="3.05")
    loop._record_step_cost(str(tmp_path), "s3://b/demo", "eu-west-2", "step-1", 600, 1.2, before, after)
    rec = json.loads((tmp_path / "step-costs.jsonl").read_text().splitlines()[0])
    assert rec["step_id"] == "step-1" and rec["seconds"] == 600 and rec["researcher_claude_usd"] == 1.2
    assert rec["delta_claude_cost_usd"] == 1.5 and rec["delta_worker_cost_usd"] == 0.2
    assert rec["delta_spent_usd"] == 1.75 and rec["spent_usd_after"] == 3.05
    assert calls and calls[0][-3].endswith("/logs/step-costs.jsonl")
