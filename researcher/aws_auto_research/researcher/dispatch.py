"""Dispatch — launch the run-experiment sub-agent(s). Replaces dispatch-experiment.sh + dispatch-all.sh.

One experiment = one nested `claude -p` process (runner chosen by the project's experiment_runner),
bounded by the `timeout` binary, streamed to its own CloudWatch stream + S3 archive. The worker is
stopped via the runner's finalize() in a finally (was a bash EXIT trap). dispatch_all fans out one
sub-agent per (exp, worker) concurrently and returns the result array.
"""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from ..config import Settings
from ..control_plane import ControlPlaneClient
from ..experiment_runners import runner_for
from ..logs import LogShipper

_ALLOWED_TOOLS = "Read,Edit,Write,Bash,Glob,Grep,Skill,Task,WebSearch,WebFetch"


def _stream_subprocess(argv: list[str], env: dict, shipper: LogShipper) -> int:
    p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    assert p.stdout is not None
    for line in p.stdout:
        shipper.ship_line(line.rstrip("\n"))
    p.wait()
    return p.returncode


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


def dispatch_experiment(exp_id: str, branch: str | None = None, worker_id: str | None = None) -> dict:
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

    subenv = env.copy()
    subenv.update({
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "RESULT_FILE": result_file, "EXP_ID": exp_id, "BRANCH": s.branch, "WORKER_ID": wid,
        "RUN_EXPERIMENT_DIR": os.path.expanduser(f"~/.claude/skills/{skill}"), "AWS_REGION": s.region,
    })
    argv = [
        "timeout", "-k", "30", f"{mins}m",
        "claude", "-p", _prompt(skill, exp_id, s.branch, wid),
        "--model", env.get("CLAUDE_MODEL", ""),
        "--allowedTools", _ALLOWED_TOOLS,
        "--max-turns", "80",
        "--max-budget-usd", env.get("SUBAGENT_BUDGET_USD", "3.00"),
        "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
    ]
    print(f"[dispatch] launching {skill} sub-agent for {exp_id} (runner={runner_name}, timeout={mins}m)")
    shipper = LogShipper(s.region, env.get("LOG_GROUP", ""), stream, log_file, echo=False)
    rc = _stream_subprocess(argv, subenv, shipper)
    timed_out = rc in (124, 137)

    # Archive the full sub-agent log to S3 (durable, retrievable in the web app).
    subprocess.run(["aws", "s3", "cp", log_file, agent_log_s3, "--region", s.region],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # GUARANTEED cleanup: stop the worker (ec2) / no-op (local), even on timeout/crash.
    try:
        runner_for(s, client).finalize()
    except Exception:  # noqa: BLE001
        pass

    return _assemble_result(result_file, exp_id, stream, agent_log_s3, timed_out, mins)


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
    with ThreadPoolExecutor(max_workers=len(triples)) as ex:
        futs = {ex.submit(dispatch_experiment, e, b, w): i for i, (e, b, w) in enumerate(triples)}
        for f, i in futs.items():
            try:
                results[i] = f.result()
            except Exception as e:  # noqa: BLE001
                results[i] = {"experiment_id": triples[i][0], "status": "crash",
                              "notes": f"dispatch error: {e}"}
    return results
