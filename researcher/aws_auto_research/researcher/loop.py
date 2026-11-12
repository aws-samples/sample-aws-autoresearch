"""The autonomous research loop — replaces researcher-loop.sh.

Each iteration is a bounded `claude -p /research-step`. The loop enforces the budget + patience
guards and persists working-tree changes. It does NOT compute spend: every cost is metered into the
project record by agent_api —
  - Claude: each `claude -p` here is streamed through a ClaudeCostMeter (costs.py),
  - researcher instance: instance-meter.sh, launched at boot, meters this box's running time,
  - workers: each worker meters its own running time the same way.
The loop just re-reads `spent_usd` and keeps every invocation inside the remaining budget.
Prints everything to stdout; boot pipes that through the CloudWatch shipper.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from ..config import Settings
from ..control_plane import ControlPlaneClient, Project
from ..costs import MIN_CLAUDE_BUDGET_USD, ClaudeCostMeter, budget_for_claude, claude_reporter, run_metered
from .spend import budget_state, remaining_usd, steps_since_last_keep

ALLOWED_TOOLS = "Read,Edit,Write,Bash,Glob,Grep,Skill,Task,WebSearch,WebFetch"
_MIN_PAPER_BUDGET_USD = 0.50
_PAPER_RESERVE_MAX_FRACTION = 0.20   # never hold back more than this share of the budget for the paper


def _sh(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, **kw)


def _git_pull(d: str) -> None:
    if os.path.isdir(os.path.join(d, ".git")):
        _sh(["git", "-C", d, "pull", "--ff-only"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _persist_state(dirs: list[str], msg: str) -> None:
    for d in dirs:
        if not os.path.isdir(os.path.join(d, ".git")):
            continue
        _sh(["git", "-C", d, "add", "-A"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        staged = _sh(["git", "-C", d, "diff", "--cached", "--quiet"])
        if staged.returncode == 0:
            print(f"persist: nothing to commit in {d}")
            continue
        _sh(["git", "-C", d, "commit", "-m", msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pushed = _sh(["git", "-C", d, "push"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"persist: {'pushed' if pushed.returncode == 0 else 'push failed for'} {d}")


def _archive(log_file: str, s3: str, region: str) -> None:
    if s3 and os.path.exists(log_file) and os.path.getsize(log_file) > 0:
        _sh(["aws", "s3", "cp", log_file, f"{s3}/logs/researcher.jsonl", "--region", region],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _to_stdout(raw: bytes) -> None:
    # Verbatim pass-through: boot pipes our stdout to the separate `aws-research log-ship` process,
    # and the web app parses each stream-json object (user-interface/src/lib/logFormat.tsx).
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _claude(env: dict, client: ControlPlaneClient, project_id: str, invocation_id: str,
            prompt: str, max_turns: int, budget_usd: float) -> float:
    """Run one metered `claude -p`; return its cost (USD)."""
    model = env.get("CLAUDE_MODEL", "")
    argv = [
        "claude", "-p", prompt,
        "--model", model, "--allowedTools", ALLOWED_TOOLS,
        "--max-turns", str(max_turns), "--max-budget-usd", f"{budget_usd:.4f}",
        "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
    ]
    meter = ClaudeCostMeter(claude_reporter(client, project_id, invocation_id), model_id=model)
    run_metered(argv, env, meter, _to_stdout)
    return meter.total_usd


def _paper_reserve(env: dict, project: Project) -> float:
    """Budget held back for the paper — capped at a fraction of the budget, so a small budget is not
    consumed entirely by the reserve (which would skip research and go straight to the paper)."""
    if not project.write_paper:
        return 0.0
    want = float(env.get("PAPER_BUDGET_USD", "6.00"))
    budget = float(project.budget_usd or 0)
    return min(want, _PAPER_RESERVE_MAX_FRACTION * budget) if budget > 0 else want


_TOTALS = ("claude_cost_usd", "researcher_cost_usd", "worker_cost_usd", "spent_usd")


def _totals(p: Project) -> dict:
    return {k: float(getattr(p, k) or 0) for k in _TOTALS}


def _record_step_cost(log_dir: str, s3: str, region: str, step_id: str, secs: int,
                      claude_step: float, before: Project, after: Project) -> None:
    """Persist this iteration's cost: deltas of the metered totals across the step (they include the
    step's experiments, sub-agents, the researcher's and the workers' running time), plus the
    researcher's own Claude cost. One JSON line per step → {s3}/logs/step-costs.jsonl."""
    b, a = _totals(before), _totals(after)
    rec = {"step_id": step_id, "seconds": secs, "researcher_claude_usd": round(claude_step, 6),
           **{f"delta_{k}": round(a[k] - b[k], 6) for k in _TOTALS},
           "spent_usd_after": round(a["spent_usd"], 6)}
    path = os.path.join(log_dir, "step-costs.jsonl")
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        return
    if s3:
        _sh(["aws", "s3", "cp", path, f"{s3}/logs/step-costs.jsonl", "--region", region],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _print_spend(client: ControlPlaneClient, project_id: str, note: str = "") -> Project:
    p = client.get_project(project_id)
    print(f"spend to date: ${float(p.spent_usd or 0):.4f} of ${float(p.budget_usd or 0):.2f} "
          f"(claude ${float(p.claude_cost_usd or 0):.4f} + researcher ${float(p.researcher_cost_usd or 0):.4f} "
          f"+ workers ${float(p.worker_cost_usd or 0):.4f}){note}")
    return p


def _maybe_write_paper(env: dict, client: ControlPlaneClient, project: Project, reason: str) -> None:
    if not project.write_paper:
        return
    project = client.get_project(project.project_id)
    remaining = remaining_usd(project.spent_usd, project.budget_usd)
    budget = budget_for_claude(remaining, float(env.get("PAPER_BUDGET_USD", "6.00")), env.get("CLAUDE_MODEL", ""))
    if budget < _MIN_PAPER_BUDGET_USD:
        print(f"=== research done ({reason}) + write_paper=true — SKIPPING the paper: only "
              f"${remaining or 0:.2f} of budget left ===")
        return
    print(f"=== research done ({reason}) + write_paper=true — writing the scientific paper "
          f"(max ${budget:.2f}) ===")
    work = os.path.expanduser("~/workspace")
    _git_pull(f"{work}/code")
    _git_pull(f"{work}/wiki")
    subenv = env.copy()
    subenv["CLAUDE_CODE_USE_BEDROCK"] = "1"
    prompt = (
        f"Research on this project is complete ({reason}). Invoke the /scientific-paper-writing skill "
        f"and follow it exactly: write the paper from the wiki, the experiments ledger, "
        f"report/status.md and the code; build the PDF; commit the LaTeX sources + figure scripts to "
        f"the code repo; upload the figures and the built PDF to S3. You are autonomous; do not ask "
        f"for confirmation."
    )
    cost = _claude(subenv, client, project.project_id, f"paper-{int(time.time())}", prompt, 150, budget)
    print(f"paper: claude cost ${cost:.4f}")
    s3 = project.s3_data
    if s3 and _sh(["aws", "s3", "ls", f"{s3}/paper/paper.pdf", "--region", env.get("AWS_REGION", "")],
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        client.update_project(project.project_id, {"paper_pdf_s3_uri": f"{s3}/paper/paper.pdf"})
        print(f"paper: PDF available at {s3}/paper/paper.pdf")
    else:
        print("paper: no PDF produced (build may have failed) — see the 'paper' log stream")


def loop() -> None:
    env = os.environ.copy()
    s = Settings.from_env()
    client = ControlPlaneClient(s.region)
    work = os.path.expanduser("~/workspace")
    code, wiki = f"{work}/code", f"{work}/wiki"
    step_dir = os.path.expanduser("~/.claude/skills/research-step")
    log_file = env.get("RESEARCHER_LOG_FILE", f"{work}/logs/researcher.jsonl")
    s3 = client.get_project(s.project_id).s3_data

    print(f"Research loop started | project={s.project_id} | model={env.get('CLAUDE_MODEL', '')}")
    done_reason = None  # set only when research COMPLETES (budget/patience); stays None on a manual Stop
    project = None
    while True:
        project = client.get_project(s.project_id)
        if project.status != "active":
            print(f"Project status is '{project.status}' (not active) — ending loop.")
            break
        reserve = _paper_reserve(env, project)
        if budget_state(project.spent_usd, project.budget_usd, reserve) == "OVER":
            extra = f" (keeping ${reserve:.2f} back for the paper)" if reserve else ""
            print(f"Budget reached (spent ${float(project.spent_usd or 0):.4f} of budget "
                  f"${project.budget_usd}{extra}) — pausing.")
            done_reason = "budget exhausted"
            break
        patience = int(project.patience or 10)
        nokeep = steps_since_last_keep(client.list_experiments(s.project_id))
        if patience > 0 and nokeep >= patience:
            print(f"Patience exhausted ({nokeep} steps since last kept >= {patience}) — pausing.")
            done_reason = "patience exhausted"
            break

        _git_pull(code)
        _git_pull(wiki)
        step_id = "step-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        print(f"=== research-step iteration {step_id} ({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}) ===")
        # Bound this step's own Claude spend by what is left (sub-agents are bounded at dispatch).
        remaining = remaining_usd(project.spent_usd, project.budget_usd, reserve)
        budget = budget_for_claude(remaining, float(env.get("MAX_ITER_BUDGET_USD", "5.00")),
                                   env.get("CLAUDE_MODEL", ""))
        if budget < MIN_CLAUDE_BUDGET_USD:
            print(f"Budget reached (only ${remaining or 0:.4f} left for research) — pausing.")
            done_reason = "budget exhausted"
            break
        subenv = env.copy()
        subenv.update({"STEP_ID": step_id, "RESEARCH_STEP_DIR": step_dir, "CLAUDE_CODE_USE_BEDROCK": "1",
                       "PAPER_RESERVE_USD": f"{reserve:.4f}"})
        prompt = (
            "Run one research iteration now. Invoke the /research-step skill and follow it exactly. This "
            "iteration's step id is $STEP_ID (already set in your environment). Do not ask for confirmation; "
            "you are autonomous."
        )
        t0 = time.time()
        cost = _claude(subenv, client, s.project_id, step_id, prompt, 120, budget)
        step_secs = int(time.time() - t0)

        _persist_state([code, wiki], f"step {step_id}: autonomous research iteration")
        after = _print_spend(client, s.project_id,
                             f"; this step: {step_secs}s, researcher claude ${cost:.4f} (cap ${budget:.2f})")
        _record_step_cost(os.path.dirname(log_file), s3, s.region, step_id, step_secs, cost, project, after)
        _archive(log_file, s3, s.region)
        print("=== iteration complete ===")

    # Research completed (budget/patience) — optionally write the paper, then pause. A manual Stop
    # leaves done_reason None: no paper, no status change (the human already set it).
    if done_reason and project is not None:
        _maybe_write_paper(env, client, project, done_reason)
        client.update_project(s.project_id, {"status": "paused"})
        _print_spend(client, s.project_id, " — final")

    _archive(log_file, s3, s.region)
    print("Research loop finished.")
