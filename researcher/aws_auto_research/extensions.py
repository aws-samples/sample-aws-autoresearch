"""Domain extensions on the researcher (design.md "Domain extensions").

At boot (as root) each extension the project uses is installed from ``s3://<bucket>/_platform/extensions/<name>/``:
its client wheels into their own venv (``/opt/ar-ext/<name>``, its commands linked onto ``/usr/local/bin``), its
skill into ``~/.claude/skills/<name>/``, and its environment (the manifest's ``client.env``, with ``{username}``,
``{password_param}`` and ``{project_id}`` filled in from the project's record) into the research loop's environment
and ``/etc/profile.d``. Credentials stay in the project's SSM path; the client reads them when it needs them.

While the loop runs, a poller reads each metered extension's own ledger (its ``cost.command``, e.g.
``rs costs --json``) and reports it to agent_api (``meter_extension``), so the extension's calls count in the
project's spend whatever the agent does. Command hooks (``health``, ``on_stop``) run here; credential hooks
(``disable-user``...) run in the control plane.
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import subprocess
import threading
from typing import Callable

PREFIX = "_platform/extensions"
ROOT = "/opt/ar-ext"
BUILTIN_HOOKS = {"disable-user", "enable-user", "delete-user"}
POLL_S = 120


def bucket_of(s3_data: str) -> str:
    """``s3://bucket/project`` -> ``bucket``."""
    return s3_data[5:].split("/", 1)[0] if s3_data.startswith("s3://") else ""


def resolve_env(doc: dict, rec: dict, project_id: str) -> dict[str, str]:
    """The manifest's client environment with the project's identity filled in."""
    fill = {"username": rec.get("username") or rec.get("identity") or "", "password_param": rec.get("password_param", ""),
            "project_id": project_id}
    out = {}
    for k, v in ((doc.get("client") or {}).get("env") or {}).items():
        s = str(v)
        for name, val in fill.items():
            s = s.replace("{" + name + "}", val)
        out[str(k)] = s
    return out


def profile_script(envs: dict[str, str]) -> str:
    return "".join(f"export {k}={shlex.quote(v)}\n" for k, v in sorted(envs.items()))


def install(name: str, bucket: str, region: str, home: str, user: str, log: Callable[[str], None],
            run=subprocess.run) -> dict | None:
    """Fetch, install and return the extension's manifest (None when it cannot be installed)."""
    src = f"{ROOT}/{name}/src"
    os.makedirs(src, exist_ok=True)
    r = run(["aws", "s3", "sync", f"s3://{bucket}/{PREFIX}/{name}/", src, "--only-show-errors", "--region", region])
    if r.returncode != 0 or not os.path.exists(f"{src}/extension.json"):
        log(f"[ext] {name}: could not fetch s3://{bucket}/{PREFIX}/{name}/ — the extension is unavailable")
        return None
    with open(f"{src}/extension.json") as f:
        doc = json.load(f)
    client = doc.get("client") or {}
    wheels = sorted({w for pat in client.get("wheels") or [] for w in glob.glob(os.path.join(src, pat))})
    venv = f"{ROOT}/{name}/venv"
    if wheels:
        if not os.path.exists(f"{venv}/bin/python"):
            run(["python3", "-m", "venv", venv])
        # the wheels' dependencies, then the wheels themselves again: a rebuilt package often keeps its version
        rc = run([f"{venv}/bin/pip", "install", "--quiet", *wheels]).returncode or \
            run([f"{venv}/bin/pip", "install", "--quiet", "--force-reinstall", "--no-deps", *wheels]).returncode
        log(f"[ext] {name}: client {'installed' if rc == 0 else 'FAILED to install'} ({len(wheels)} wheels)")
        for cmd in client.get("commands") or []:
            if os.path.exists(f"{venv}/bin/{cmd}"):
                run(["ln", "-sf", f"{venv}/bin/{cmd}", f"/usr/local/bin/{cmd}"])
    skill = os.path.join(src, str(doc.get("skill") or "skill/"))
    if os.path.isdir(skill):
        dest = f"{home}/.claude/skills/{name}"
        run(["rm", "-rf", dest])
        run(["cp", "-r", skill, dest])
        run(["chown", "-R", f"{user}:{user}", dest])
    return doc


def run_command(cmd: str, env: dict[str, str], timeout_s: float = 120, run=subprocess.run) -> tuple[int, str]:
    """Run a command hook (as the caller) with the extension's environment; (exit code, output tail)."""
    try:
        r = run(["bash", "-lc", cmd], env={**os.environ, **env}, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout_s:.0f} s"
    return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-2000:]


def hook(doc: dict, event: str, env: dict[str, str], log: Callable[[str], None], run=subprocess.run) -> None:
    cmd = str((doc.get("hooks") or {}).get(event) or "")
    if not cmd or cmd in BUILTIN_HOOKS:
        return
    rc, out = run_command(cmd, env, run=run)
    log(f"[ext] {doc['name']}: {event} hook `{cmd}` exited {rc}" + (f": {out.strip()[-300:]}" if rc else ""))


def read_costs(doc: dict, env: dict[str, str], run=subprocess.run) -> tuple[float, float] | None:
    """(finished USD, running USD) from the extension's own ledger, or None when it cannot be read."""
    cost = doc.get("cost") or {}
    if not cost.get("command"):
        return None
    rc, out = run_command(str(cost["command"]), env, timeout_s=90, run=run)
    if rc != 0:
        return None
    try:
        d = json.loads(out[out.index("{"):])
        return float(d.get(cost.get("final_field", "finalUsd")) or 0), float(d.get(cost.get("active_field", "activeUsd")) or 0)
    except (ValueError, TypeError):
        return None


class CostPoller:
    """Polls every metered extension's ledger and meters it into the project (agent_api meter_extension)."""

    def __init__(self, client, project_id: str, exts: dict[str, tuple[dict, dict[str, str]]],
                 log: Callable[[str], None] = print, interval_s: float = POLL_S) -> None:
        self.client, self.project_id, self.exts, self.log, self.interval_s = client, project_id, exts, log, interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll(self) -> dict[str, float]:
        seen = {}
        for name, (doc, env) in self.exts.items():
            costs = read_costs(doc, env)
            if costs is None:
                continue
            r = self.client.invoke_raw({"action": "meter_extension", "project_id": self.project_id, "extension": name,
                                        "final_usd": f"{costs[0]:.6f}", "active_usd": f"{costs[1]:.6f}"})
            if not r.get("ok"):
                self.log(f"[ext] {name}: cost report refused: {r.get('error')}")
                continue
            seen[name] = costs[0] + costs[1]
            if r.get("budget_hard_stop"):
                self.log(f"[ext] budget reached: {r['budget_hard_stop']}")
        return seen

    def start(self) -> None:
        if not self.exts:
            return

        def run():
            while not self._stop.wait(self.interval_s):
                try:
                    self.poll()
                except Exception as e:  # noqa: BLE001 - never kill the loop over a cost report
                    self.log(f"[ext] cost poll failed: {e!r}")
        self._thread = threading.Thread(target=run, name="extension-costs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def manifests(project_extensions: dict, src_root: str = ROOT) -> dict[str, dict]:
    """The installed manifests of the project's extensions (as boot left them under /opt/ar-ext)."""
    out = {}
    for name in project_extensions or {}:
        path = f"{src_root}/{name}/src/extension.json"
        if os.path.exists(path):
            with open(path) as f:
                out[name] = json.load(f)
    return out


def loop_extensions(project, src_root: str = ROOT) -> dict[str, tuple[dict, dict[str, str]]]:
    """{name: (manifest, environment)} of the project's enabled extensions, for the loop's cost poller."""
    exts = getattr(project, "extensions", None) or {}
    return {n: (doc, resolve_env(doc, exts[n], project.project_id))
            for n, doc in manifests(exts, src_root).items() if (doc.get("cost") or {}).get("command")}
