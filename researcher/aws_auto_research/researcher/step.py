"""research-step mechanics — the ledger/branch/context helpers the /research-step skill calls.

Replaces the research-step/scripts/*.sh. Pure payload/format helpers (unit-tested) are separated
from the git/control-plane IO. All git ops run in the researcher's code dir.
"""

from __future__ import annotations

import json
import subprocess
import time

from ..config import Settings
from ..control_plane import ControlPlaneClient

_STATUS_ENUM = {"provisioning", "setup", "active", "paused", "completed", "provision_failed"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- pure helpers (tested) ----------------------------------------------------
def experiment_item(exp_id: str, worker_id: str, hypothesis: str, description: str, now: str) -> dict:
    return {"state": "running", "hypothesis": hypothesis, "description": description,
            "branch": f"experiment/{exp_id}", "worker_id": worker_id,
            "log_stream": f"experiment/{exp_id}", "created_at": now}


def finalize_updates(result: dict, state: str, now: str) -> dict:
    """Terminal ledger update from a dispatch result + the analysed state; drop null fields."""
    u = {
        "state": state,
        "metric_value": result.get("metric_value"),
        "peak_vram_mb": result.get("peak_vram_mb"),
        "run_seconds": result.get("run_seconds", 0),
        "worker_uptime_seconds": result.get("worker_uptime_seconds", 0),
        "cost_usd": result.get("cost_usd"),
        "log_stream": result.get("log_stream"),
        "agent_log_s3_uri": result.get("agent_log_s3_uri"),
        "run_log_s3_uri": result.get("run_log_s3_uri"),
        "completed_at": now,
    }
    return {k: v for k, v in u.items() if v is not None}


def experiment_summary(e: dict) -> dict:
    return {"id": e.get("experiment_id"), "state": e.get("state"),
            "metric": e.get("metric_value"), "vram": e.get("peak_vram_mb"),
            "desc": e.get("description")}


# --- git IO -------------------------------------------------------------------
def _git(s: Settings, *args: str, check: bool = True) -> None:
    p = subprocess.run(["git", "-C", s.code_dir, *args], capture_output=True, text=True)
    if p.stdout:
        print(p.stdout, end="")
    if p.returncode != 0:
        if p.stderr:
            print(p.stderr, end="")
        if check:
            raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")


# --- commands -----------------------------------------------------------------
def create_experiment(client: ControlPlaneClient, s: Settings, exp_id, worker_id, hyp, change) -> None:
    item = experiment_item(exp_id, worker_id, hyp, change, _now())
    print(json.dumps(client.create_experiment(s.project_id, exp_id, item)))


def experiment_branch(s: Settings, exp_id: str) -> None:
    _git(s, "checkout", "main")
    _git(s, "pull", "--ff-only")
    _git(s, "checkout", "-B", f"experiment/{exp_id}")
    print(f"on branch experiment/{exp_id} (from main)")


def experiment_push(s: Settings, exp_id: str, msg: str) -> None:
    _git(s, "commit", "-am", f"experiment/{exp_id}: {msg}", "--allow-empty")
    _git(s, "push", "-u", "origin", f"experiment/{exp_id}")
    _git(s, "checkout", "main")
    print(f"pushed experiment/{exp_id}")


def finalize_experiment(client: ControlPlaneClient, s: Settings, state: str, result_json: str) -> None:
    r = json.loads(result_json)
    exp_id = r.get("experiment_id")
    if not exp_id:
        raise RuntimeError("result_json has no experiment_id")
    print(json.dumps(client.update_experiment(s.project_id, exp_id, finalize_updates(r, state, _now()))))
    if state in ("discarded", "crashed"):
        s3 = client.get_project(s.project_id).s3_data
        if s3:
            print(f"pruning {state} checkpoint: {s3}/checkpoints/{exp_id}/")
            subprocess.run(["aws", "s3", "rm", "--recursive", f"{s3}/checkpoints/{exp_id}/",
                            "--region", s.region], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def keep_best(client: ControlPlaneClient, s: Settings, exp_id: str, best: str, msg: str) -> None:
    _git(s, "fetch", "origin", f"experiment/{exp_id}")
    _git(s, "checkout", "main")
    _git(s, "pull", "--ff-only")
    _git(s, "merge", "--no-ff", f"origin/experiment/{exp_id}", "-m", f"keep {exp_id}: {msg}")
    _git(s, "push", "origin", "main")
    client.update_project(s.project_id, {"best_metric": best})
    print(f"merged experiment/{exp_id} into main; best_metric={best}")


def update_project(client: ControlPlaneClient, s: Settings, field: str, value: str) -> None:
    if field == "status" and value not in _STATUS_ENUM:
        raise RuntimeError(f"'status' is a lifecycle enum, not a summary — put notes in report/status.md (got: {value})")
    print(json.dumps(client.update_project(s.project_id, {field: value})))


def project_info(client: ControlPlaneClient, s: Settings, field: str | None = None) -> None:
    project = client.get_project(s.project_id).model_dump()
    if field:
        v = project.get(field, "")
        print("" if v is None else v)
    else:
        print(json.dumps(project, indent=2))


def guidance(client: ControlPlaneClient, s: Settings, action: str, gid: str | None = None) -> None:
    if action == "list":
        print(json.dumps(client.get_guidance(s.project_id)))
    elif action == "done":
        if not gid:
            raise RuntimeError("usage: guidance done <guidance_id>")
        print(json.dumps(client.mark_guidance_processed(s.project_id, gid)))
    else:
        raise RuntimeError("usage: guidance list | guidance done <id>")


def list_experiments(client: ControlPlaneClient, s: Settings, state: str | None = None) -> None:
    states = [state] if state else None
    for e in client.list_experiments(s.project_id, states):
        print(json.dumps(experiment_summary(e)))


def pull_context(client: ControlPlaneClient, s: Settings) -> None:
    p = client.get_project(s.project_id)
    workers = ["local"] if (p.experiment_runner or "ec2") == "local" else list(p.worker_instance_ids)
    print(f"PROJECT_ID={s.project_id}")
    print(f"EXPERIMENT_RUNNER={p.experiment_runner or 'ec2'}")
    print(f"N_WORKERS={len(workers)}")
    print(f"WORKERS={' '.join(workers)}")
    print(f"BEST_METRIC={p.best_metric or 'none'}")
    print(f"SPENT_USD={p.spent_usd or 0}")
    print(f"BUDGET_USD={p.budget_usd or 0}")
    print(f"STATUS={p.status or 'unknown'}")
    print(f"S3_DATA={p.s3_data or ''}")
    print("--- recent experiments ---")
    list_experiments(client, s)
