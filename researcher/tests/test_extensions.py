"""Domain extensions on the researcher: install, environment, hooks and cost polling (no AWS)."""

import json
import os
import subprocess

from aws_auto_research import extensions as ext

DOC = {"name": "robots-sim",
       "client": {"wheels": ["client/*.whl"], "commands": ["rs"],
                  "env": {"ROBOTS_SIM_USER": "{username}", "ROBOTS_SIM_PASSWORD_PARAM": "{password_param}",
                          "ROBOTS_SIM_REGION": "eu-west-2", "TAG": "proj-{project_id}"}},
       "skill": "skill/", "cost": {"type": "poll", "command": "rs costs --json"},
       "hooks": {"health": "rs get /v1/me", "on_stop": "rs cancel --mine", "on_budget_exhausted": "disable-user"}}
REC = {"status": "ok", "identity": "ar-demo", "username": "ar-demo",
       "password_param": "/auto-research/projects/demo/ext/robots-sim/password"}


def done(rc=0, out=""):
    return subprocess.CompletedProcess([], rc, out, "")


def test_the_client_environment_names_the_projects_identity_not_its_secret():
    env = ext.resolve_env(DOC, REC, "demo")
    assert env == {"ROBOTS_SIM_USER": "ar-demo", "ROBOTS_SIM_PASSWORD_PARAM": REC["password_param"],
                   "ROBOTS_SIM_REGION": "eu-west-2", "TAG": "proj-demo"}
    assert "export ROBOTS_SIM_USER=ar-demo\n" in ext.profile_script(env)
    assert ext.bucket_of("s3://auto-research-1-eu-west-2/demo") == "auto-research-1-eu-west-2"


def test_install_fetches_installs_links_and_places_the_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(ext, "ROOT", str(tmp_path / "opt"))
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:3] == ["aws", "s3", "sync"]:                 # what the extension's stack uploaded
            src = argv[4]
            os.makedirs(f"{src}/client", exist_ok=True)
            os.makedirs(f"{src}/skill", exist_ok=True)
            open(f"{src}/client/robots_sim_client-0.1.0-py3-none-any.whl", "w").close()
            open(f"{src}/skill/SKILL.md", "w").write("# rs")
            json.dump(DOC, open(f"{src}/extension.json", "w"))
        return done()

    doc = ext.install("robots-sim", "bkt", "eu-west-2", str(tmp_path / "home"), "ubuntu", print, run=run)
    assert doc["name"] == "robots-sim"
    flat = [" ".join(c) for c in calls]
    assert any("s3://bkt/_platform/extensions/robots-sim/" in c for c in flat)
    assert any(c.startswith(f"{tmp_path}/opt/robots-sim/venv/bin/pip install") and c.endswith(".whl") for c in flat)
    assert any(c.startswith(f"cp -r {tmp_path}/opt/robots-sim/src/skill/ {tmp_path}/home/.claude/skills/robots-sim") for c in flat)
    # a missing extension (its stack was removed) does not stop the boot
    assert ext.install("gone", "bkt", "eu-west-2", str(tmp_path / "home"), "ubuntu", print,
                       run=lambda argv, **kw: done(1)) is None


def test_costs_come_from_the_extensions_own_ledger():
    out = json.dumps({"user": "ar-demo", "items": [], "finalUsd": 1.25, "activeUsd": 0.5})
    assert ext.read_costs(DOC, {}, run=lambda argv, **kw: done(0, "signing in...\n" + out)) == (1.25, 0.5)
    assert ext.read_costs(DOC, {}, run=lambda argv, **kw: done(1, "401")) is None
    assert ext.read_costs(DOC, {}, run=lambda argv, **kw: done(0, "not json")) is None
    assert ext.read_costs({"name": "x"}, {}) is None


def test_the_poller_reports_each_metered_extension(monkeypatch):
    sent = []

    class Client:
        def invoke_raw(self, payload):
            sent.append(payload)
            return {"ok": True, "budget_hard_stop": ["robots-sim: disable-user ar-demo"]}

    monkeypatch.setattr(ext, "read_costs", lambda doc, env, run=None: (2.0, 0.75))
    logs = []
    p = ext.CostPoller(Client(), "demo", {"robots-sim": (DOC, {})}, log=logs.append)
    assert p.poll() == {"robots-sim": 2.75}
    assert sent == [{"action": "meter_extension", "project_id": "demo", "extension": "robots-sim",
                     "final_usd": "2.000000", "active_usd": "0.750000"}]
    assert "budget reached" in logs[0]


def test_command_hooks_run_and_credential_hooks_are_left_to_the_platform():
    ran, logs = [], []
    ext.hook(DOC, "on_stop", {"A": "1"}, logs.append, run=lambda argv, **kw: ran.append(argv) or done(0))
    assert ran == [["bash", "-lc", "rs cancel --mine"]] and "exited 0" in logs[0]
    ext.hook(DOC, "on_budget_exhausted", {}, logs.append, run=lambda argv, **kw: ran.append(argv) or done(0))
    assert len(ran) == 1                                       # disable-user runs in agent_api, not here
