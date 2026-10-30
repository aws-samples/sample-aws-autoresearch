"""Budget / patience / spend — pure functions (were jq/awk in researcher-loop.sh)."""

from __future__ import annotations

import re

_STEP_RE = re.compile(r"^(step-\d{8}-\d{6})")


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def budget_state(spent, budget) -> str:
    """'OVER' iff a positive budget has been reached; else 'OK'. (blank/<=0 budget = no cap)."""
    s, b = _f(spent), _f(budget)
    return "OVER" if (b > 0 and s >= b) else "OK"


def ledger_cost(experiments: list[dict]) -> float:
    """Sum of per-experiment cost_usd (0 in local mode; worker cost in ec2 mode)."""
    return round(sum(_f(e.get("cost_usd")) for e in experiments or []), 4)


def steps_since_last_keep(experiments: list[dict]) -> int:
    """Leading run of steps (newest-first) that kept nothing — the patience counter.

    A step = the `step-YYYYMMDD-HHMMSS` prefix of experiment_id; a step 'kept' if any of its
    experiments finalized as state 'kept'. Returns 0 if the latest step kept something.
    """
    kept_by_step: dict[str, bool] = {}
    for e in experiments or []:
        m = _STEP_RE.match(e.get("experiment_id", "") or "")
        if not m:
            continue
        sid = m.group(1)
        kept_by_step[sid] = kept_by_step.get(sid, False) or (e.get("state") == "kept")
    if not kept_by_step:
        return 0
    # newest step first; count leading Falses until the first kept step.
    n = 0
    for sid in sorted(kept_by_step, reverse=True):
        if kept_by_step[sid]:
            break
        n += 1
    return n


def step_cost(seconds: float, hourly_rate: float) -> float:
    return round(max(0.0, float(seconds)) / 3600.0 * float(hourly_rate), 4)
