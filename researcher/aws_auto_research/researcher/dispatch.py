"""Dispatch — launch the run-experiment sub-agent(s). Replaces dispatch-experiment.sh + dispatch-all.sh.

One experiment = one nested `claude -p` process (runner chosen by the project's experiment_runner),
bounded by the `timeout` binary, streamed to its own CloudWatch stream + S3 archive. The worker is
stopped via the runner's finalize() in a finally (was a bash EXIT trap). dispatch_all fans out one
sub-agent per (exp, worker) concurrently and returns the result array.

Cost accounting happens HERE, in code, never via the agent:
- the sub-agent's Claude usage is metered (costs.ClaudeCostMeter) into the project's claude total and
  attributed to the experiment (agent_cost_usd) — also when it times out or crashes;
- in ec2 mode the worker's running time during the experiment is measured from EC2 state
  (worker_usage.WorkerUsage) and attributed to the experiment (worker_cost_usd). The project's billed
  worker total is metered on the worker itself (instance-meter.sh), so this is attribution only;
- the sub-agent's --max-budget-usd is bounded by the project's remaining budget.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from .. import aws
from ..config import Settings
from ..control_plane import ControlPlaneClient
from ..costs import MIN_CLAUDE_BUDGET_USD, ClaudeCostMeter, budget_for_claude, claude_reporter, run_metered
from ..experiment_runners import runner_for
from ..logs import LogShipper
from ..worker_usage import WorkerUsage
from .spend import remaining_usd

_ALLOWED_TOOLS = "Read,Edit,Write,Bash,Glob,Grep,Skill,Task,WebSearch,WebFetch"


def _prompt(skill: str, exp_id: str, branch: str, worker_id: str) -> str:
    if skill == "run-ec2-experiment":
        return (f"You are the run-experiment sub-agent for experiment {exp_id} on branch {branch}, "
                f"using GPU worker {worker_id} (already set as $WORKER_ID). Invoke /{skill} and follow "
                f"it exactly: start that worker, run the experiment, and stop it. Write your final "
                f"result JSON to the file at $RESULT_FILE. You are autonomous; do not ask for confirmation.")
    return (f"You are the run-experiment sub-agent for experiment {exp_id} on branch {branch}, running "
            f"LOCALLY on this instance (there is no separate GPU worker). Invoke /{skill} and follow it "
            f"exactly: run the experiment here and write your final result JSON to the file at "
            f"$RESULT_FILE. You are autonomous; do not ask for confirmation.")


def _ensure_worker_stopped(s: Settings, client: ControlPlaneClient, usage: WorkerUsage | None) -> None:
    """GUARANTEED cleanup: stop the worker (ec2) / no-op (local), even on timeout/crash — and in ec2
    mode verify it actually left `running`, re-issuing the stop if needed. Never raises: it runs in a
    `finally`, and an exception here must not discard the experiment's result."""
    try:
        runner_for(s, client).finalize()
    except Exception as e:  # noqa: BLE001
        print(f"[dispatch] finalize failed for {s.exp_id}: {e}", flush=True)
    if usage is None:
        return
    try:
        for attempt in range(3):
            if usage.wait_until_down(timeout_s=240):
                return
            print(f"[dispatch] worker {s.worker_id} still {usage.state} after stop — re-issuing stop "
                  f"(attempt {attempt + 2})", flush=True)
            aws.stop_instance(s.region, s.worker_id)
        print(f"[dispatch] WARNING: worker {s.worker_id} did not stop; its time keeps being metered "
              f"until it does (watchdog backstop)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[dispatch] could not verify worker {s.worker_id} stopped: {e}", flush=True)


def dispatch_experiment(exp_id: str, branch: str | None = None, worker_id: str | None = None,
                        n_concurrent: int = 1) -> dict:
    env = os.environ.copy()
    s = Settings.from_env()
    s.exp_id = exp_id
    s.branch = branch or f"experiment/{exp_id}"
    client = ControlPlaneClient(s.region)
    project = client.get_project(s.project_id)

    runner_name = project.experiment_runner or "ec2"
    s3 = project.s3_data.rstrip("/")
    log_dir = os.path.expanduser("~/workspace/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{exp_id}.jsonl")
    result_file = os.path.join(log_dir, f"{exp_id}.result.json")
    open(result_file, "w").close()
    stream = f"experiment/{exp_id}"
    agent_log_s3 = f"{s3}/experiments/{exp_id}/agent.jsonl"
    mins = int(project.experiment_timeout_minutes or 15)

    if runner_name == "ec2":
        wid = worker_id or s.worker_id
        if not wid or wid == "None":
            wid = (project.worker_instance_ids or [""])[0]
        skill = "run-ec2-experiment"
    else:
        wid = "local"
        skill = "run-local-experiment"
    s.worker_id = wid
    s.result_file = result_file

    # Budget: this sub-agent's share of what is left (the step's own spend is already metered in).
    model = env.get("CLAUDE_MODEL", "")
    remaining = remaining_usd(project.spent_usd, project.budget_usd, env.get("PAPER_RESERVE_USD", "0"))
    share = None if remaining is None else remaining / max(1, n_concurrent)
    budget = budget_for_claude(share, float(env.get("SUBAGENT_BUDGET_USD", "3.00")), model)
    if budget < MIN_CLAUDE_BUDGET_USD:
        print(f"[dispatch] NOT launching {exp_id}: project budget exhausted", flush=True)
        return {"experiment_id": exp_id, "status": "crash", "notes": "not run: project budget exhausted",
                "log_stream": stream, "agent_log_s3_uri": agent_log_s3,
                "worker_uptime_seconds": 0, "worker_cost_usd": "0", "agent_cost_usd": "0", "cost_usd": "0"}

    subenv = env.copy()
    subenv.update({
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "RESULT_FILE": result_file, "EXP_ID": exp_id, "BRANCH": s.branch, "WORKER_ID": wid,
        "RUN_EXPERIMENT_DIR": os.path.expanduser(f"~/.claude/skills/{skill}"), "AWS_REGION": s.region,
    })
    argv = [
        "timeout", "-k", "30", f"{mins}m",
        "claude", "-p", _prompt(skill, exp_id, s.branch, wid),
        "--model", model,
        "--allowedTools", _ALLOWED_TOOLS,
        "--max-turns", "80",
        "--max-budget-usd", f"{budget:.4f}",
        "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
    ]
    print(f"[dispatch] launching {skill} sub-agent for {exp_id} (runner={runner_name}, "
          f"timeout={mins}m, claude cap=${budget:.2f})", flush=True)
    shipper = LogShipper(s.region, env.get("LOG_GROUP", ""), stream, log_file, echo=False)
    # A fresh id per dispatch: a re-dispatched experiment is a NEW invocation (charged in full), not a
    # continuation of the first run's high-water mark.
    run_id = f"exp-{exp_id}-{time.time_ns()}"
    meter = ClaudeCostMeter(claude_reporter(client, s.project_id, run_id, exp_id), model_id=model)
    usage = None
    if runner_name == "ec2" and wid:
        usage = WorkerUsage(lambda: aws.instance_state_and_launch(s.region, wid)).start()

    rc = 1
    try:
        rc = run_metered(argv, subenv, meter,
                         lambda raw: shipper.ship_line(raw.decode("utf-8", "replace").rstrip("\n")))
    finally:
        _ensure_worker_stopped(s, client, usage)
    timed_out = rc in (124, 137)

    # Archive the full sub-agent log to S3 (durable, retrievable in the web app).
    subprocess.run(["aws", "s3", "cp", log_file, agent_log_s3, "--region", s.region],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    result = _assemble_result(result_file, exp_id, stream, agent_log_s3, timed_out, mins)
    # Metered costs — authoritative, they override anything the sub-agent wrote.
    agent_cost = meter.total_usd
    worker_secs, worker_cost = 0, 0.0
    if usage is not None:
        worker_secs = usage.stop()
        r = client.record_worker_usage(s.project_id, exp_id, worker_secs, run_id)
        if r.get("ok") and r.get("recorded"):
            worker_cost = float(r.get("worker_cost_usd") or 0)
        else:
            print(f"[dispatch] record_worker_usage({exp_id}): {r.get('error') or r.get('reason')}", flush=True)
    result.update({
        "worker_uptime_seconds": worker_secs,
        "worker_cost_usd": f"{worker_cost:.4f}",
        "agent_cost_usd": f"{agent_cost:.4f}",
        "cost_usd": f"{worker_cost + agent_cost:.4f}",
    })
    print(f"[dispatch] {exp_id}: agent ${agent_cost:.4f} + worker {worker_secs}s ${worker_cost:.4f}", flush=True)
    return result


def _assemble_result(result_file, exp_id, stream, agent_log_s3, timed_out, mins) -> dict:
    content = ""
    if os.path.exists(result_file):
        content = open(result_file).read().strip()
    if content:
        try:
            d = json.loads(content)
            d["log_stream"] = stream
            d["agent_log_s3_uri"] = agent_log_s3
            return d
        except json.JSONDecodeError:
            pass
    if timed_out:
        notes = f"experiment exceeded the {mins}-minute timeout and was terminated at dispatch"
        status = "timeout"
    else:
        notes = "sub-agent produced no result"
        status = "crash"
    return {"experiment_id": exp_id, "status": status, "notes": notes,
            "log_stream": stream, "agent_log_s3_uri": agent_log_s3}


def dispatch_all(triples: list[tuple[str, str, str]]) -> list[dict]:
    """triples: (exp_id, branch, worker_id). Run each sub-agent concurrently; return results in order."""
    results: list[dict] = [{} for _ in triples]
    if not triples:
        return results
    n = len(triples)
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(dispatch_experiment, e, b, w, n): i for i, (e, b, w) in enumerate(triples)}
        for f, i in futs.items():
            try:
                results[i] = f.result()
            except Exception as e:  # noqa: BLE001
                results[i] = {"experiment_id": triples[i][0], "status": "crash",
                              "notes": f"dispatch error: {e}"}
    return results
