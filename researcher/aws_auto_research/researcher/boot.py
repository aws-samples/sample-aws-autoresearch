"""Researcher boot — replaces bootstrap.sh. Runs as root (from the UserData launcher).

First boot: install Claude Code + uv + light Python libs, set git identity, clone the code+wiki repos.
Every boot: pull. Then launch the research loop as the ubuntu user, ship its output to the researcher
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


def _u(cmd: str) -> int:
    """Run a shell command as the ubuntu user, with ~/.local/bin (claude/uv) and the venv on PATH.
    Prepending ~/.local/bin also silences the Claude installer's "not in your PATH" setup note."""
    full = f'export PATH="$HOME/.local/bin:{VENV}/bin:$PATH"; {cmd}'
    return subprocess.run(["sudo", "-u", U, "bash", "-lc", full]).returncode


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

    if not os.path.exists(MARKER):
        log(f"=== AI Researcher first-time bootstrap (instance {instance_id}) ===")
        _u("curl -fsSL https://claude.ai/install.sh | bash")
        _u("curl -LsSf https://astral.sh/uv/install.sh | sh")
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
    # Two decoupled processes over a kernel pipe — mirrors the old `researcher-loop.sh 2>&1 | log-ship.sh`.
    # The loop (and the `claude` it spawns) writes stdout+stderr straight into the pipe; a SEPARATE
    # `aws-research log-ship` process drains it and PutLogEvents to CloudWatch. Python is out of the byte
    # path, so a slow/throttled CloudWatch call can never block (and thus freeze) the research loop.
    ship_env = {**os.environ, "AWS_REGION": region, "LOG_GROUP": log_group,
                "LOG_STREAM": stream, "LOG_FILE": log_file}
    p_loop = subprocess.Popen(loop_argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    p_ship = subprocess.Popen([f"{VENV}/bin/aws-research", "log-ship"], stdin=p_loop.stdout, env=ship_env)
    p_loop.stdout.close()   # only the shipper holds the read end (loop gets SIGPIPE if the shipper dies)
    p_loop.wait()
    p_ship.wait()

    log("=== Research loop ended; stopping instance ===")
    subprocess.run(["shutdown", "-h", "now"])
