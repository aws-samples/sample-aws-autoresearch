"""The autonomous research loop — replaces researcher-loop.sh.

Each iteration is a bounded `claude -p /research-step`. The loop enforces the budget + patience
guards, persists working-tree changes, and accounts spend = ledger experiment cost + the
researcher's own per-step runtime cost (researcher_cost_usd). Prints everything to stdout; boot pipes
that through the CloudWatch shipper, so the loop itself does no shipping.
"""

from __future__ import annotations

import os
import subprocess
import time

from ..config import Settings
from ..control_plane import ControlPlaneClient
from ..rates import hourly_usd
from .spend import budget_state, ledger_cost, step_cost, steps_since_last_keep

ALLOWED_TOOLS = "Read,Edit,Write,Bash,Glob,Grep,Skill,Task,WebSearch,WebFetch"


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


def _stream(argv: list[str], env: dict) -> int:
    """Run a `claude -p --output-format stream-json` child, letting its stdout+stderr flow STRAIGHT
    THROUGH to our own stdout (which boot pipes to the separate `aws-research log-ship` process). No
    Python sits in the byte path — the raw stream-json is shipped verbatim and the web app parses each
    object into a readable row (see user-interface/src/lib/logFormat.tsx)."""
    return subprocess.run(argv, env=env).returncode


def _run_step(env: dict, step_dir: str, step_id: str) -> None:
    subenv = env.copy()
    subenv.update({"STEP_ID": step_id, "RESEARCH_STEP_DIR": step_dir, "CLAUDE_CODE_USE_BEDROCK": "1"})
    argv = [
        "claude", "-p",
        "Run one research iteration now. Invoke the /research-step skill and follow it exactly. This "
        "iteration's step id is $STEP_ID (already set in your environment). Do not ask for confirmation; "
        "you are autonomous.",
        "--model", subenv.get("CLAUDE_MODEL", ""), "--allowedTools", ALLOWED_TOOLS,
        "--max-turns", "120", "--max-budget-usd", subenv.get("MAX_ITER_BUDGET_USD", "5.00"),
        "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
    ]
    _stream(argv, subenv)


def _maybe_write_paper(env: dict, client: ControlPlaneClient, project, reason: str) -> None:
    if not project.write_paper:
        return
    print(f"=== research done ({reason}) + write_paper=true — writing the scientific paper ===")
    work = os.path.expanduser("~/workspace")
    _git_pull(f"{work}/code")
    _git_pull(f"{work}/wiki")
    subenv = env.copy()
    subenv["CLAUDE_CODE_USE_BEDROCK"] = "1"
    argv = [
        "claude", "-p",
        f"Research on this project is complete ({reason}). Invoke the /scientific-paper-writing skill "
        f"and follow it exactly: write the paper from the wiki, the experiments ledger, "
        f"report/status.md and the code; build the PDF; commit the LaTeX sources + figure scripts to "
        f"the code repo; upload the figures and the built PDF to S3. You are autonomous; do not ask "
        f"for confirmation.",
        "--model", subenv.get("CLAUDE_MODEL", ""), "--allowedTools", ALLOWED_TOOLS,
        "--max-turns", "150", "--max-budget-usd", subenv.get("PAPER_BUDGET_USD", "6.00"),
        "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
    ]
    _stream(argv, subenv)
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
    while True:
        project = client.get_project(s.project_id)
        if project.status != "active":
            print(f"Project status is '{project.status}' (not active) — ending loop.")
            break
        if budget_state(project.spent_usd, project.budget_usd) == "OVER":
            print(f"Budget reached (spent ${project.spent_usd} >= budget ${project.budget_usd}) — pausing.")
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
        t0 = time.time()
        _run_step(env, step_dir, step_id)
        step_secs = int(time.time() - t0)

        _persist_state([code, wiki], f"step {step_id}: autonomous research iteration")

        rrate = hourly_usd(project.researcher_instance_type)
        sc = step_cost(step_secs, rrate)
        rcost = round(float(project.researcher_cost_usd or 0) + sc, 4)
        ledger = ledger_cost(client.list_experiments(s.project_id))
        spent = round(ledger + rcost, 4)
        client.update_project(s.project_id, {"researcher_cost_usd": f"{rcost:.4f}", "spent_usd": f"{spent:.4f}"})
        print(f"spend to date: ${spent:.4f} (experiments ${ledger:.4f} + researcher ${rcost:.4f}; "
              f"this step {step_secs}s @ ${rrate}/hr = ${sc:.4f})")
        _archive(log_file, s3, s.region)
        print("=== iteration complete ===")

    # Research completed (budget/patience) — optionally write the paper, then pause. A manual Stop
    # leaves done_reason None: no paper, no status change (the human already set it).
    if done_reason:
        _maybe_write_paper(env, client, project, done_reason)
        client.update_project(s.project_id, {"status": "paused"})

    _archive(log_file, s3, s.region)
    print("Research loop finished.")
