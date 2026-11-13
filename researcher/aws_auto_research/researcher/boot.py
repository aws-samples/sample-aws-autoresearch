"""Researcher boot — replaces bootstrap.sh. Runs as root (from the UserData launcher).

First boot: install Claude Code + uv + light Python libs, set git identity, clone the code+wiki repos.
Every boot: start the instance cost meter, then pull. Then launch the research loop as the ubuntu user, ship its output to the researcher
CloudWatch stream, and stop the instance when the loop exits (session paused/completed).
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.request

import boto3

from ..logs import send_log

U = "ubuntu"
HOME_DIR = f"/home/{U}"
WORK_DIR = f"{HOME_DIR}/workspace"
MARKER = f"{HOME_DIR}/.researcher-bootstrap-complete"
VENV = "/opt/ar"  # isolated researcher venv (created in boot.sh); holds aws-research + the python libs below
_IMDS = "http://169.254.169.254/latest"


def _imds(path: str, token: str) -> str:
    return urllib.request.urlopen(
        urllib.request.Request(f"{_IMDS}/meta-data/{path}", headers={"X-aws-ec2-metadata-token": token}),
        timeout=2,
    ).read().decode().strip()


INSTALL_LOG = "/var/log/ar-installers.log"


def _u(cmd: str, out_path: str | None = None) -> int:
    """Run a shell command as the ubuntu user, with ~/.local/bin (claude/uv) and the venv on PATH.
    Prepending ~/.local/bin also silences the Claude installer's "not in your PATH" setup note.

    `out_path`: send the command's stdout+stderr to this file (opened here, as root, in append mode)
    instead of inheriting boot.sh's shared `>(tee /var/log/ar-boot.log)` pipe. Used for the third-party
    installers: the O_NONBLOCK flag lives on the pipe's shared open file description, so a tool that
    sets it on its stdout (and does not restore it) leaves it set for every later writer of that pipe —
    the suspected trigger of the 2026-11-12 crash, which happened on a project's first (installing) boot."""
    full = f'export PATH="$HOME/.local/bin:{VENV}/bin:$PATH"; {cmd}'
    argv = ["sudo", "-u", U, "bash", "-lc", full]
    if out_path is None:
        return subprocess.run(argv).returncode
    with open(out_path, "ab") as f:
        f.write(f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} $ {cmd}\n".encode())
        f.flush()
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT).returncode


def _ensure_blocking_stdio(log, when: str, fds: tuple[int, ...] = (1, 2)) -> list[int]:
    """Clear O_NONBLOCK on our stdout/stderr (boot.sh's tee pipe, inherited by the log shipper) if
    anything set it. Returns the fds that had it set; each occurrence is logged as a WARNING so we
    learn when (and after which step) something flips it."""
    import fcntl
    fixed = []
    for fd in fds:
        try:
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            if fl & os.O_NONBLOCK:
                fcntl.fcntl(fd, fcntl.F_SETFL, fl & ~os.O_NONBLOCK)
                fixed.append(fd)
        except OSError:
            continue
    if fixed:
        log(f"[boot] WARNING: O_NONBLOCK was set on fd(s) {fixed} (boot log pipe) {when} — cleared it "
            f"(a non-blocking pipe makes large writes fail with EAGAIN)")
    return fixed


def _u_retry(cmd: str, n: int = 5) -> None:
    """Run a ubuntu command, retrying transient failures (e.g. a throttled CodeCommit clone)."""
    for i in range(1, n + 1):
        if _u(cmd) == 0:
            return
        print(f"[boot] retry {i}/{n}: {cmd[:60]}", flush=True)
        time.sleep(10)


def _pip_install(pkgs: list[str], n: int = 3) -> None:
    """pip install into the isolated researcher venv (/opt/ar, created in boot.sh) with retries.
    Installing in a venv (not system-wide as root) avoids the root-user + non-PEP440 (devscripts)
    pip warnings and PEP-668 entirely. The venv bin is first on the loop/agent PATH (see loop_env)."""
    pip = f"{VENV}/bin/pip"
    for i in range(1, n + 1):
        if subprocess.run([pip, "install", "--quiet", *pkgs]).returncode == 0:
            return
        print(f"[boot] pip retry {i}/{n}: {' '.join(pkgs)}", flush=True)
        time.sleep(10)


def _geo_model(region: str) -> str:
    geo = "eu" if region.startswith("eu-") else "apac" if region.startswith("ap-") else "us"
    return f"{geo}.anthropic.claude-opus-4-8"


METER_SCRIPT = f"{HOME_DIR}/.auto-research/instance-meter.sh"


def _start_instance_meter(region: str, log) -> None:
    """Meter THIS instance's running time (from its EC2 LaunchTime, so boot is billed too) into the
    project's researcher_cost_usd — as its own systemd unit, so it keeps running (and flushes on
    shutdown) independently of the loop, including the paper step and a Stop mid-step."""
    if not os.path.exists(METER_SCRIPT):
        log(f"WARNING: {METER_SCRIPT} missing — researcher instance time is NOT being metered")
        return
    r = subprocess.run(
        ["systemd-run", "--unit=ar-instance-meter", "--collect",
         "-p", "After=network-online.target", "-p", "Wants=network-online.target",
         "-p", "Restart=on-failure", "-p", "RestartSec=30",
         f"--setenv=AWS_REGION={region}", "/bin/bash", METER_SCRIPT],
        capture_output=True, text=True)
    if r.returncode != 0:
        log(f"instance meter not started (already running?): {r.stderr.strip()[:200]}")


def boot() -> None:
    token = urllib.request.urlopen(
        urllib.request.Request(f"{_IMDS}/api/token", method="PUT",
                               headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"}), timeout=2,
    ).read().decode()
    instance_id = _imds("instance-id", token)
    region = os.environ.get("AWS_REGION") or _imds("placement/region", token)

    ec2 = boto3.client("ec2", region_name=region)
    tags = {t["Key"]: t["Value"] for t in ec2.describe_tags(
        Filters=[{"Name": "resource-id", "Values": [instance_id]}])["Tags"]}
    project_id = tags.get("ProjectId", "")
    model = tags.get("Model") or _geo_model(region)
    code_repo, wiki_repo = f"{project_id}-code", f"{project_id}-wiki"
    log_group = f"/auto-research/{project_id}"
    stream = f"researcher-{instance_id}"

    def log(m: str) -> None:
        send_log(region, log_group, stream, m)

    _start_instance_meter(region, log)

    if not os.path.exists(MARKER):
        log(f"=== AI Researcher first-time bootstrap (instance {instance_id}) ===")
        # Third-party installers write to their own file, NOT the shared boot-log pipe (see _u).
        for name, cmd in (("Claude Code", "curl -fsSL https://claude.ai/install.sh | bash"),
                          ("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh")):
            rc = _u(cmd, out_path=INSTALL_LOG)
            log(f"[boot] {name} installer exited rc={rc} (output: {INSTALL_LOG})"
                + ("" if rc == 0 else " — WARNING: install failed"))
        _ensure_blocking_stdio(log, "after the installers")
        _pip_install(["git-remote-codecommit"])   # git remote helper for codecommit:: clone/pull
        # symlink the venv's grcm onto PATH so system `git` finds it when cloning/pulling as ubuntu
        subprocess.run(["ln", "-sf", f"{VENV}/bin/git-remote-codecommit", "/usr/local/bin/git-remote-codecommit"])
        _pip_install(["matplotlib", "numpy", "pandas"])
        _u('git config --global user.email "researcher@auto-research"')
        _u('git config --global user.name "AI Researcher"')
        _u(f"mkdir -p {WORK_DIR}")
        if not os.path.isdir(f"{WORK_DIR}/code/.git"):
            _u_retry(f'git clone "codecommit::{region}://{code_repo}" "{WORK_DIR}/code"')
        if not os.path.isdir(f"{WORK_DIR}/wiki/.git"):
            _u_retry(f'git clone "codecommit::{region}://{wiki_repo}" "{WORK_DIR}/wiki"')
        with open(f"{HOME_DIR}/.claude.json", "w") as f:
            f.write('{ "projects": { "%s/code": { "hasTrustDialogAccepted": true } } }' % WORK_DIR)
        subprocess.run(["chown", f"{U}:{U}", f"{HOME_DIR}/.claude.json"])
        open(MARKER, "w").close()
        log("=== Bootstrap complete ===")
    else:
        log("=== AI Researcher waking (already bootstrapped) ===")
        _u(f'git -C "{WORK_DIR}/code" pull --ff-only')
        _u(f'git -C "{WORK_DIR}/wiki" pull --ff-only')

    _ssm = boto3.client("ssm", region_name=region)
    try:
        api_url = _ssm.get_parameter(Name="/auto-research/api-url")["Parameter"]["Value"]
    except Exception:  # noqa: BLE001
        api_url = ""
    # Per-project agent-api capability token (isolation). Read from this project's SSM path
    # (readable only by this project's role) and pass to the loop so control_plane binds every
    # agent_api call to this project_id. Absent for legacy projects → agent_api allows.
    try:
        agent_token = _ssm.get_parameter(
            Name=f"/auto-research/projects/{project_id}/agent-token", WithDecryption=True)["Parameter"]["Value"]
    except Exception:  # noqa: BLE001
        agent_token = ""

    log_file = f"{WORK_DIR}/logs/researcher.jsonl"
    _u(f"mkdir -p {WORK_DIR}/logs")
    log(f"--- Starting research loop (stream: {stream}) ---")

    loop_env = {
        "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": region, "HOME": HOME_DIR,
        # venv bin first: `python3` resolves to the venv (has matplotlib/numpy/pandas), plus aws-research + grcm
        "PATH": f"{VENV}/bin:{HOME_DIR}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        # unbuffered so the loop's own progress lines flush per-line into the pipe (not block-buffered)
        "PYTHONUNBUFFERED": "1",
        "API_URL": api_url, "PROJECT_ID": project_id, "INSTANCE_ID": instance_id,
        "AR_AGENT_TOKEN": agent_token,
        "CLAUDE_MODEL": model, "LOG_GROUP": log_group, "RESEARCHER_LOG_FILE": log_file,
        "BASH_DEFAULT_TIMEOUT_MS": os.environ.get("BASH_DEFAULT_TIMEOUT_MS", "1800000"),
        "BASH_MAX_TIMEOUT_MS": os.environ.get("BASH_MAX_TIMEOUT_MS", "1800000"),
    }
    loop_argv = ["sudo", "-u", U, "env"] + [f"{k}={v}" for k, v in loop_env.items()] + \
                ["aws-research", "researcher", "loop"]
    ship_env = {**os.environ, "AWS_REGION": region, "LOG_GROUP": log_group,
                "LOG_STREAM": stream, "LOG_FILE": log_file}

    def status() -> str | None:
        return _project_status(region, project_id, agent_token)

    def pause() -> bool:
        return _set_paused(region, project_id, agent_token)

    def run_once() -> tuple[int, int, float]:
        # Re-checked before EVERY (re)start: the shipper inherits these fds as its stdout/stderr.
        _ensure_blocking_stdio(log, "before starting the research loop + log shipper")
        return _run_loop_once(loop_argv, ship_env)

    supervise(run_once, status, pause, log)

    log("=== Research loop ended; stopping instance ===")
    subprocess.run(["shutdown", "-h", "now"])


# --- loop supervision ------------------------------------------------------------------------------
# The loop exits 0 on every planned path (budget/patience → it sets 'paused' itself; Stop button →
# a human already set 'paused'). A NON-ZERO exit is a crash: the project is still 'active' but nothing
# is running, and since start_session only starts 'setup'/'paused' projects, it cannot even be
# restarted from the UI. So: restart the loop (its first action re-checks status/budget/patience, so a
# restart is always safe), and after repeated quick crashes give up by setting 'paused' (UI shows it,
# Start works again) instead of shutting down silently with a stale 'active'.
MAX_FAST_CRASHES = 3          # consecutive crashes, each within FAST_CRASH_S of its start → give up
FAST_CRASH_S = 600            # a run that survived longer than this resets the crash counter
RESTART_BACKOFF_S = (30, 60, 120)

_RC_HINTS = {
    120: "the loop could not flush stdout at exit — typically a broken pipe (log shipper gone)",
    -9: "SIGKILL (OOM killer or manual kill)",
    -15: "SIGTERM",
    1: "uncaught Python exception (traceback should be in the researcher log stream above)",
}


def _run_loop_once(loop_argv: list[str], ship_env: dict) -> tuple[int, int, float]:
    """Run the loop once; return (loop rc, shipper rc, seconds).

    Two decoupled processes over a kernel pipe — mirrors the old `researcher-loop.sh 2>&1 | log-ship.sh`.
    The loop (and the `claude` it spawns) writes stdout+stderr straight into the pipe; a SEPARATE
    `aws-research log-ship` process drains it and PutLogEvents to CloudWatch. Python is out of the byte
    path, so a slow/throttled CloudWatch call can never block (and thus freeze) the research loop."""
    t0 = time.time()
    p_loop = subprocess.Popen(loop_argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    p_ship = subprocess.Popen([f"{VENV}/bin/aws-research", "log-ship"], stdin=p_loop.stdout, env=ship_env)
    p_loop.stdout.close()   # only the shipper holds the read end (loop gets EPIPE if the shipper dies)
    rc_loop = p_loop.wait()
    rc_ship = p_ship.wait()
    return rc_loop, rc_ship, time.time() - t0


def _project_status(region: str, project_id: str, token: str) -> str | None:
    """Current project status via agent_api, or None if it cannot be read."""
    from ..control_plane import ControlPlaneClient
    try:
        r = ControlPlaneClient(region).invoke_raw(
            {"action": "get_project", "project_id": project_id, "_token": token})
        return (r.get("project") or {}).get("status")
    except Exception:  # noqa: BLE001
        return None


def _set_paused(region: str, project_id: str, token: str) -> bool:
    from ..control_plane import ControlPlaneClient
    try:
        r = ControlPlaneClient(region).invoke_raw(
            {"action": "update_project", "project_id": project_id,
             "updates": {"status": "paused"}, "_token": token})
        return bool(r.get("ok"))
    except Exception:  # noqa: BLE001
        return False


def supervise(run_once, get_status, set_paused, log, sleep=time.sleep) -> str:
    """Run the loop until it exits cleanly, the project leaves 'active', or it keeps crashing.
    Returns the outcome: 'clean' | 'not-active' | 'gave-up'. Every decision is logged."""
    fast_crashes = 0
    attempt = 0
    while True:
        attempt += 1
        rc_loop, rc_ship, secs = run_once()
        if rc_ship != 0:
            log(f"[supervisor] WARNING: log shipper exited rc={rc_ship} (run {attempt}) — research log "
                f"output after that point was lost; see /var/log/ar-boot.log / the 'boot' stream")
        if rc_loop == 0:
            if attempt > 1:
                log(f"[supervisor] research loop exited cleanly on run {attempt}")
            return "clean"

        hint = _RC_HINTS.get(rc_loop, "unexpected exit")
        st = get_status()
        log(f"[supervisor] WARNING: research loop CRASHED (rc={rc_loop}: {hint}) after {secs:.0f}s "
            f"on run {attempt}; project status={st or 'unknown'}")
        if st is not None and st != "active":
            log(f"[supervisor] project is '{st}' (not active) — not restarting")
            return "not-active"

        fast_crashes = fast_crashes + 1 if secs < FAST_CRASH_S else 1
        if fast_crashes >= MAX_FAST_CRASHES:
            ok = set_paused()
            log(f"[supervisor] ERROR: {fast_crashes} consecutive crashes within {FAST_CRASH_S}s each — "
                f"giving up; status set to 'paused' {'OK' if ok else 'FAILED (still active?)'} so the "
                f"project can be restarted from the UI after investigation")
            return "gave-up"
        wait = RESTART_BACKOFF_S[min(fast_crashes, len(RESTART_BACKOFF_S)) - 1]
        log(f"[supervisor] restarting the research loop in {wait}s "
            f"(crash {fast_crashes}/{MAX_FAST_CRASHES - 1} restarts allowed)")
        sleep(wait)
