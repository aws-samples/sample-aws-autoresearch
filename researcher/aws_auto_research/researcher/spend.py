"""Budget / patience — pure functions. Spend itself is METERED by agent_api (see costs.py and
instance-meter.sh); nothing here computes cost."""

from __future__ import annotations

import re

_STEP_RE = re.compile(r"^(step-\d{8}-\d{6})")


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def budget_state(spent, budget, reserve=0.0) -> str:
    """'OVER' iff a positive budget has been reached (keeping `reserve` back, e.g. for the paper);
    else 'OK'. (blank/<=0 budget = no cap)."""
    s, b = _f(spent), _f(budget)
    return "OVER" if (b > 0 and s + max(0.0, _f(reserve)) >= b) else "OK"


def remaining_usd(spent, budget, reserve=0.0) -> float | None:
    """Budget left for new work (None = no cap)."""
    b = _f(budget)
    if b <= 0:
        return None
    return max(0.0, b - _f(spent) - max(0.0, _f(reserve)))


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
