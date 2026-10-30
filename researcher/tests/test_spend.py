from aws_auto_research.researcher.spend import (
    budget_state, ledger_cost, step_cost, steps_since_last_keep,
)


def test_budget_state():
    assert budget_state("10", "5") == "OVER"
    assert budget_state("5", "5") == "OVER"      # >= is over
    assert budget_state("4.99", "5") == "OK"
    assert budget_state("100", "0") == "OK"      # 0 budget = no cap
    assert budget_state("100", "") == "OK"       # blank = no cap


def test_ledger_cost_tolerant():
    exps = [{"cost_usd": "0.10"}, {"cost_usd": 0.05}, {"cost_usd": None}, {}, {"cost_usd": "x"}]
    assert ledger_cost(exps) == 0.15
    assert ledger_cost([]) == 0.0


def _e(step, some_id, state):
    return {"experiment_id": f"{step}-{some_id}", "state": state}


def test_steps_since_last_keep():
    # latest step kept -> 0
    exps = [_e("step-20260101-000001", "a", "kept"), _e("step-20260101-000002", "b", "discarded"),
            _e("step-20260101-000002", "c", "kept")]
    assert steps_since_last_keep(exps) == 0

    # two most-recent steps kept nothing; the one before kept -> 2
    exps = [
        _e("step-20260101-000001", "a", "kept"),
        _e("step-20260101-000002", "b", "discarded"),
        _e("step-20260101-000003", "c", "crashed"),
    ]
    assert steps_since_last_keep(exps) == 2

    # nothing ever kept -> count all distinct steps
    exps = [_e("step-20260101-000001", "a", "discarded"), _e("step-20260101-000002", "b", "crashed")]
    assert steps_since_last_keep(exps) == 2

    assert steps_since_last_keep([]) == 0


def test_step_cost():
    assert step_cost(3600, 0.526) == 0.526
    assert step_cost(0, 5.0) == 0.0
