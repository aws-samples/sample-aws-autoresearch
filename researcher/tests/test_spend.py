from aws_auto_research.researcher.spend import budget_state, remaining_usd, steps_since_last_keep


def test_budget_state():
    assert budget_state("10", "5") == "OVER"
    assert budget_state("5", "5") == "OVER"      # >= is over
    assert budget_state("4.99", "5") == "OK"
    assert budget_state("100", "0") == "OK"      # 0 budget = no cap
    assert budget_state("100", "") == "OK"       # blank = no cap


def test_budget_state_keeps_reserve():
    assert budget_state("4", "10", reserve=6) == "OVER"   # 4 + 6 reserved for the paper >= 10
    assert budget_state("3.9", "10", reserve=6) == "OK"
    assert budget_state("100", "0", reserve=6) == "OK"    # no cap


def test_remaining_usd():
    assert remaining_usd("2.5", "10") == 7.5
    assert remaining_usd("2.5", "10", reserve="6") == 1.5
    assert remaining_usd("12", "10") == 0.0               # never negative
    assert remaining_usd("12", "0") is None               # no cap


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
