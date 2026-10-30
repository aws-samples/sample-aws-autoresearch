"""aws-research — CLI entrypoint. The skill scripts are thin shims over these commands.

`runner` group: one experiment's steps, dispatched to the ec2/local runner chosen by the project's
experiment_runner field. Env contract (preset by the dispatcher): PROJECT_ID, AWS_REGION, EXP_ID,
BRANCH, WORKER_ID, RESULT_FILE — same as the shell scripts.
"""

from __future__ import annotations

import json
import sys

import typer

from .config import Settings
from .control_plane import ControlPlaneClient
from .experiment_runners import runner_for

app = typer.Typer(help="AI Researcher runtime.", no_args_is_help=True)
runner_app = typer.Typer(help="Run one experiment's steps (ec2 | local).", no_args_is_help=True)
researcher_app = typer.Typer(help="Researcher boot + autonomous loop.", no_args_is_help=True)
step_app = typer.Typer(help="research-step mechanics (ledger, branches, context).", no_args_is_help=True)
app.add_typer(runner_app, name="runner")
app.add_typer(researcher_app, name="researcher")
app.add_typer(step_app, name="step")


def _cs():
    from .config import Settings as _S
    s = _S.from_env()
    return ControlPlaneClient(s.region), s


def _step_guard(fn) -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)


@step_app.command("create-experiment")
def step_create_experiment(exp_id: str, worker_id: str, hypothesis: str, change: str) -> None:
    from .researcher import step
    _step_guard(lambda: step.create_experiment(*_cs(), exp_id, worker_id, hypothesis, change))


@step_app.command("experiment-branch")
def step_experiment_branch(exp_id: str) -> None:
    from .researcher import step
    _step_guard(lambda: step.experiment_branch(_cs()[1], exp_id))


@step_app.command("experiment-push")
def step_experiment_push(exp_id: str, message: str) -> None:
    from .researcher import step
    _step_guard(lambda: step.experiment_push(_cs()[1], exp_id, message))


@step_app.command("finalize-experiment")
def step_finalize_experiment(state: str, result_json: str) -> None:
    from .researcher import step
    _step_guard(lambda: step.finalize_experiment(*_cs(), state, result_json))


@step_app.command("keep-best")
def step_keep_best(exp_id: str, best_metric: str, message: str) -> None:
    from .researcher import step
    _step_guard(lambda: step.keep_best(*_cs(), exp_id, best_metric, message))


@step_app.command("update-project")
def step_update_project(field: str, value: str) -> None:
    from .researcher import step
    _step_guard(lambda: step.update_project(*_cs(), field, value))


@step_app.command("project-info")
def step_project_info(field: str = typer.Argument(None)) -> None:
    from .researcher import step
    _step_guard(lambda: step.project_info(*_cs(), field))


@step_app.command("guidance")
def step_guidance(action: str, guidance_id: str = typer.Argument(None)) -> None:
    from .researcher import step
    _step_guard(lambda: step.guidance(*_cs(), action, guidance_id))


@step_app.command("list-experiments")
def step_list_experiments(state: str = typer.Argument(None)) -> None:
    from .researcher import step
    _step_guard(lambda: step.list_experiments(*_cs(), state))


@step_app.command("pull-context")
def step_pull_context() -> None:
    from .researcher import step
    _step_guard(lambda: step.pull_context(*_cs()))


@step_app.command("dispatch-experiments")
def step_dispatch_experiments(pairs: list[str] = typer.Argument(None, help="EXP_ID WORKER_ID [EXP_ID WORKER_ID ...]")) -> None:
    """Run all this iteration's experiments concurrently; print the result array."""
    from .researcher.dispatch import dispatch_all
    pairs = pairs or []
    if len(pairs) % 2 != 0:
        typer.echo("ERROR: expected EXP_ID WORKER_ID pairs", err=True)
        raise typer.Exit(1)
    triples = [(pairs[i], f"experiment/{pairs[i]}", pairs[i + 1]) for i in range(0, len(pairs), 2)]
    typer.echo(json.dumps(dispatch_all(triples)))


# --- control-plane passthrough (agent-api.sh) --------------------------------
@app.command("api")
def api(payload: str = typer.Argument(..., help="JSON payload with an 'action' field")) -> None:
    """Invoke the control-plane API and print the JSON response."""
    s = Settings.from_env()
    typer.echo(json.dumps(ControlPlaneClient(s.region).invoke_raw(json.loads(payload))))


# --- log shipping (log-ship.sh) ----------------------------------------------
@app.command("log-ship")
def log_ship() -> None:
    """Ship stdin lines to a CloudWatch stream + local archive (env: LOG_GROUP, LOG_STREAM, LOG_FILE, AWS_REGION)."""
    import os
    from .logs import LogShipper
    shipper = LogShipper(os.environ["AWS_REGION"], os.environ["LOG_GROUP"], os.environ["LOG_STREAM"],
                         os.environ.get("LOG_FILE"), echo=True)
    # readline() (not `for line in sys.stdin`) so each line ships the moment it arrives — the file
    # iterator's read-ahead buffer would otherwise hold lines back until it fills, stalling the tail.
    shipper.ship(iter(sys.stdin.readline, ""))


# --- dispatch (dispatch-experiment.sh / dispatch-all.sh) ---------------------
@app.command("dispatch-experiment")
def dispatch_experiment_cmd(
    exp_id: str = typer.Argument(...),
    branch: str = typer.Argument(None),
    worker_id: str = typer.Argument(None),
) -> None:
    """Run one experiment sub-agent; print its result JSON."""
    from .researcher.dispatch import dispatch_experiment
    typer.echo(json.dumps(dispatch_experiment(exp_id, branch, worker_id)))


@app.command("dispatch-all")
def dispatch_all_cmd() -> None:
    """Read '<EXP_ID> <BRANCH> <WORKER_ID>' lines from stdin; run concurrently; print result array."""
    from .researcher.dispatch import dispatch_all
    triples = []
    for line in sys.stdin:
        parts = line.split()
        if not parts:
            continue
        exp = parts[0]
        branch = parts[1] if len(parts) > 1 else f"experiment/{exp}"
        worker = parts[2] if len(parts) > 2 else ""
        triples.append((exp, branch, worker))
    typer.echo(json.dumps(dispatch_all(triples)))


@researcher_app.command("loop")
def researcher_loop() -> None:
    """Run the autonomous research loop until the project is no longer active."""
    from .researcher.loop import loop
    loop()


@researcher_app.command("boot")
def researcher_boot() -> None:
    """First-boot install/clone, then launch the loop, then stop the instance."""
    from .researcher.boot import boot
    boot()


def _runner():
    s = Settings.from_env()
    return runner_for(s, ControlPlaneClient(s.region))


def _guard(fn) -> None:
    """Run a step; on failure print + exit 1 so the sub-agent records a crash."""
    try:
        fn(_runner())
    except Exception as e:  # noqa: BLE001
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)


@runner_app.command("prepare")
def prepare() -> None:
    """Make compute ready (ec2: start worker + wait; local: setup.sh once)."""
    _guard(lambda r: r.prepare())


@runner_app.command("sync-code")
def sync_code() -> None:
    """Check out this experiment's branch on the compute target."""
    _guard(lambda r: r.sync_code())


@runner_app.command("run-eval")
def run_eval(command: str = typer.Argument(None, help="override evaluation_command (debug)")) -> None:
    """Run evaluation_command to completion, then read metrics.json."""
    _guard(lambda r: r.run_eval(command))


@runner_app.command("exec")
def exec_(command: str = typer.Argument(..., help="command to run on the compute target")) -> None:
    """Run an arbitrary command on the compute target (debug loop / tail run.log)."""
    def _do(r):
        rc = r.exec(command)
        if rc != 0:
            raise typer.Exit(rc)
    _guard(_do)


@runner_app.command("push-branch")
def push_branch() -> None:
    """Push the exact code to origin/<branch> + archive run.log to S3."""
    _guard(lambda r: r.push_branch())


@runner_app.command("collect-artifacts")
def collect_artifacts(checkpoint: str = typer.Argument(None, help="checkpoint path to upload (only if promising)")) -> None:
    """Upload benchmarks (and optionally a checkpoint) to S3."""
    _guard(lambda r: r.collect_artifacts(checkpoint))


@runner_app.command("finalize")
def finalize() -> None:
    """Guaranteed cleanup (ec2: stop worker; local: no-op)."""
    _guard(lambda r: r.finalize())


@runner_app.command("write-result")
def write_result(
    status: str = typer.Argument(..., help="keep | discard | crash"),
    metric: str = typer.Argument(..., help="metric value or 'null'"),
    vram: str = typer.Argument(..., help="peak_vram_mb or 'null'"),
    run_seconds: int = typer.Argument(...),
    notes: str = typer.Argument(""),
    uptime: int = typer.Option(0, "--uptime", help="worker uptime seconds (ec2 cost basis; ignored for local)"),
) -> None:
    """Write the final result JSON to $RESULT_FILE."""
    _guard(lambda r: r.write_result(status, metric, vram, run_seconds, uptime, notes))


if __name__ == "__main__":
    app()
