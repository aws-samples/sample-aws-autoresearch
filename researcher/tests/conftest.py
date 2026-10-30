import json
import os

import pytest

from aws_auto_research.backends import Backend, CmdResult
from aws_auto_research.config import Settings
from aws_auto_research.control_plane import Project


class FakeBackend(Backend):
    """Records commands; returns scripted stdout for commands containing a key substring."""

    def __init__(self, outputs: dict[str, str] | None = None, fail: set[str] | None = None):
        self.commands: list[str] = []
        self.outputs = outputs or {}
        self.fail = fail or set()

    def _result(self, command: str) -> CmdResult:
        self.commands.append(command)
        for key, val in self.outputs.items():
            if key in command:
                return CmdResult(0, val, "")
        for key in self.fail:
            if key in command:
                return CmdResult(1, "", f"failed: {key}")
        return CmdResult(0, "", "")

    def run(self, command: str, timeout=None) -> CmdResult:
        return self._result(command)

    def run_raw(self, command: str, timeout=None) -> CmdResult:
        return self._result(command)

    def upload(self, local_path: str, remote_path: str) -> None:
        self.commands.append(f"upload {local_path} -> {remote_path}")

    @property
    def label(self) -> str:
        return "fake"


@pytest.fixture
def code_dir(tmp_path):
    d = tmp_path / "code"
    d.mkdir()
    (d / "auto-research.json").write_text(json.dumps({
        "evaluation_command": "cd src && uv run evaluate.py > ../run.log 2>&1",
        "metric_name": "acc", "metric_direction": "higher_is_better",
    }))
    return str(d)


@pytest.fixture
def settings(tmp_path, code_dir):
    return Settings(
        region="us-east-1", project_id="demo", exp_id="step-1-baseline",
        branch="experiment/step-1-baseline", worker_id="i-0abc",
        result_file=str(tmp_path / "result.json"), code_dir=code_dir,
    )


@pytest.fixture
def project():
    return Project(project_id="demo", s3_data="s3://bucket/demo",
                   worker_instance_type="g4dn.xlarge", ssh_key_param="/ec2/keypair/k",
                   experiment_runner="ec2")


def build(runner_cls, settings, project, backend):
    """Construct a runner with project + backend injected (no AWS/control-plane calls)."""
    r = runner_cls(settings, client=None)
    r.__dict__["project"] = project           # bypass cached_property (control plane)
    r.__dict__["backend"] = backend           # bypass cached_property (_make_backend)
    return r
