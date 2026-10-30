"""ExperimentRunner — shared per-experiment steps over a pluggable backend.

The steps (sync_code / run_eval / exec / push_branch / collect_artifacts / write_result) are
backend-agnostic: they issue shell commands in the project code dir via `self.backend`, so ec2 (SSH)
and local (subprocess) share ONE code path. Subclasses provide the backend + the mode-specific hooks
(`prepare`, `finalize`, `_cost`) and capability flags.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from functools import cached_property

from ..backends import Backend
from ..config import Settings
from ..control_plane import ControlPlaneClient, Project
from ..metrics import Metrics, MetricsError, parse_metrics
from ..result import RunResult

_METRICS_FILE = "metrics.json"


def _norm(v: str | None) -> str | None:
    """CLI passes 'null'/'' for absent metric/vram."""
    if v is None:
        return None
    return None if v.strip().lower() in ("", "null", "none") else v


class ExperimentRunner(ABC):
    name: str = "base"
    supports_interactive_debug: bool = False
    incurs_gpu_cost: bool = False

    def __init__(self, settings: Settings, client: ControlPlaneClient):
        self.s = settings
        self.client = client

    # --- cached project + backend -------------------------------------------------
    @cached_property
    def project(self) -> Project:
        return self.client.get_project(self.s.project_id)

    @cached_property
    def backend(self) -> Backend:
        return self._make_backend()

    # --- hooks subclasses implement ----------------------------------------------
    @abstractmethod
    def _make_backend(self) -> Backend: ...

    @abstractmethod
    def prepare(self) -> None:
        """Make compute READY (ec2: start+wait; local: setup.sh once). Raise on failure."""

    @abstractmethod
    def finalize(self) -> None:
        """Guaranteed cleanup (ec2: stop worker; local: no-op)."""

    @abstractmethod
    def _cost(self, run_seconds: int, uptime_seconds: int) -> tuple[float, int]:
        """Return (cost_usd, worker_uptime_seconds_to_record)."""

    # --- shared steps ------------------------------------------------------------
    def _eval_command(self) -> str:
        cfg = os.path.join(self.s.code_dir, "auto-research.json")
        with open(cfg) as f:
            cmd = json.load(f).get("evaluation_command", "")
        if not cmd:
            raise RuntimeError(f"no evaluation_command in {cfg}")
        return cmd

    def _s3(self) -> str:
        return self.project.s3_data.rstrip("/")

    def sync_code(self) -> None:
        br = self.s.branch
        if not br:
            raise RuntimeError("BRANCH not set")
        r = self.backend.run(
            f'git fetch --all --prune && git checkout -f -B "{br}" "origin/{br}" && git reset --hard "origin/{br}"'
        )
        print(r.stdout, end="")
        if not r.ok:
            raise RuntimeError(f"sync-code failed: {r.stderr.strip()}")
        print(f"checked out {br} on {self.backend.label}")

    def run_eval(self, command: str | None = None) -> Metrics | None:
        cmd = command or self._eval_command()
        # Clear any stale result so a crashed run never reports a previous run's metric.
        self.backend.run(f"rm -f {_METRICS_FILE} src/{_METRICS_FILE} 2>/dev/null; true")
        print(f"=== running evaluation on {self.backend.label} (capped by the experiment timeout at dispatch) ===")
        r = self.backend.run(cmd)
        print(r.stdout, end="")
        if r.stderr:
            print(r.stderr, end="")
        print("=== reading result ===")
        got = self.backend.run(f"cat {_METRICS_FILE} 2>/dev/null || cat src/{_METRICS_FILE} 2>/dev/null || true")
        try:
            m = parse_metrics(got.stdout)
        except MetricsError as e:
            print(f"ERROR: {e} (check run.log — this experiment cannot be scored)")
            return None
        print(f"result_source: {_METRICS_FILE}")
        print(f"metric: {m.metric}")
        if m.peak_vram_mb is not None:
            print(f"peak_vram_mb: {m.peak_vram_mb}")
        print(f"eval_status: {m.status}")
        if m.notes:
            print(f"notes: {m.notes}")
        if m.metrics:
            print(f"metrics: {json.dumps(m.metrics)}")
        return m

    def exec(self, command: str) -> int:
        """Run an arbitrary command on the compute target (debug loop / log tailing)."""
        r = self.backend.run(command)
        print(r.stdout, end="")
        if r.stderr:
            print(r.stderr, end="")
        return r.exited

    def push_branch(self) -> None:
        br, exp, s3 = self.s.branch, self.s.exp_id, self._s3()
        r = self.backend.run(f"git push origin {br}")
        print(r.stdout, end="")
        if not r.ok:
            raise RuntimeError(f"push failed: {r.stderr.strip()}")
        self.backend.run(f"[ -f run.log ] && aws s3 cp run.log {s3}/experiments/{exp}/run.log --region {self.s.region} || true")
        print(f"pushed {br} and archived run.log to {s3}/experiments/{exp}/run.log")

    def collect_artifacts(self, checkpoint: str | None = None) -> None:
        exp, s3 = self.s.exp_id, self._s3()
        self.backend.run(f"[ -d benchmarks ] && aws s3 cp benchmarks/ {s3}/benchmarks/{exp}/ --recursive --region {self.s.region} || true")
        if checkpoint:
            print(f"uploading checkpoint {checkpoint} -> {s3}/checkpoints/{exp}/")
            self.backend.run(f"aws s3 cp '{checkpoint}' {s3}/checkpoints/{exp}/ --recursive --region {self.s.region} || echo 'checkpoint upload failed (continuing)'")
        print(f"artifact collection done for {exp}")

    def write_result(self, status: str, metric: str | None, vram: str | None,
                     run_seconds: int, uptime_seconds: int = 0, notes: str = "") -> RunResult:
        cost, uptime_rec = self._cost(run_seconds, uptime_seconds)
        res = RunResult(
            experiment_id=self.s.exp_id,
            status=status,
            metric=_norm(metric),
            peak_vram_mb=_norm(vram),
            run_seconds=int(run_seconds),
            worker_uptime_seconds=uptime_rec,
            cost_usd=cost,
            run_log_s3_uri=f"{self._s3()}/experiments/{self.s.exp_id}/run.log",
            notes=notes,
        )
        if self.s.result_file:
            with open(self.s.result_file, "w") as f:
                f.write(res.to_json())
        print(f"wrote result for {self.s.exp_id} ({self.name}; cost=${cost:.4f})")
        print(res.to_json())
        return res
