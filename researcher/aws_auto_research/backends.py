"""Execution backends — run a command where the experiment lives.

The one abstraction that collapses ec2-vs-local duplication: every runner step is "run a shell
command in the project code dir" + "upload a local file". LocalBackend uses subprocess; SSHBackend
shells out to the `ssh`/`scp` binaries (already present on the boxes) — deliberately NOT paramiko/
fabric, to keep the boot install free of cffi/crypto wheels and dodge the fabric↔invoke pin risk.
Both run commands in `code_dir`, so callers pass the same command regardless of backend.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CmdResult:
    exited: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exited == 0


class Backend(ABC):
    """Runs commands in the project code dir and uploads local files."""

    @abstractmethod
    def run(self, command: str, timeout: int | None = None) -> CmdResult: ...

    @abstractmethod
    def upload(self, local_path: str, remote_path: str) -> None: ...

    @property
    @abstractmethod
    def label(self) -> str: ...


class LocalBackend(Backend):
    def __init__(self, code_dir: str):
        self.code_dir = code_dir

    def run(self, command: str, timeout: int | None = None) -> CmdResult:
        p = subprocess.run(
            ["bash", "-lc", command],
            cwd=self.code_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CmdResult(p.returncode, p.stdout, p.stderr)

    def upload(self, local_path: str, remote_path: str) -> None:
        subprocess.run(["cp", "-r", local_path, remote_path], check=False)

    @property
    def label(self) -> str:
        return f"local:{self.code_dir}"


class SSHBackend(Backend):
    def __init__(self, host: str, key_path: str, user: str = "ubuntu", code_dir: str = "~/workspace/code"):
        self.host = host
        self.key_path = key_path
        self.user = user
        self.code_dir = code_dir

    def _ssh_argv(self, remote_cmd: str) -> list[str]:
        return [
            "ssh", "-i", self.key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            f"{self.user}@{self.host}",
            remote_cmd,
        ]

    def run(self, command: str, timeout: int | None = None) -> CmdResult:
        # Run in the code dir so callers are backend-agnostic (mirrors LocalBackend's cwd).
        remote = f"cd {self.code_dir} && {command}"
        p = subprocess.run(self._ssh_argv(remote), capture_output=True, text=True, timeout=timeout)
        return CmdResult(p.returncode, p.stdout, p.stderr)

    def run_raw(self, command: str, timeout: int | None = None) -> CmdResult:
        """Run without the cd prefix — for readiness checks like `test -f ~/.worker-bootstrap-complete`."""
        p = subprocess.run(self._ssh_argv(command), capture_output=True, text=True, timeout=timeout)
        return CmdResult(p.returncode, p.stdout, p.stderr)

    def upload(self, local_path: str, remote_path: str) -> None:
        subprocess.run(
            ["scp", "-i", self.key_path, "-o", "StrictHostKeyChecking=no",
             local_path, f"{self.user}@{self.host}:{remote_path}"],
            check=False,
        )

    @property
    def label(self) -> str:
        return f"ssh:{self.user}@{self.host}"
