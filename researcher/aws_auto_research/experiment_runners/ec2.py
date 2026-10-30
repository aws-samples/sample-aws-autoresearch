"""EC2Runner — start the assigned GPU worker, run over SSH, stop it. One worker per experiment."""

from __future__ import annotations

import time

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from .. import aws
from ..backends import Backend, SSHBackend
from ..rates import cost_usd
from .base import ExperimentRunner

_READY_MARKER = "~/.worker-bootstrap-complete"


class EC2Runner(ExperimentRunner):
    name = "ec2"
    supports_interactive_debug = True   # live worker over SSH
    incurs_gpu_cost = True

    def _make_backend(self) -> Backend:
        ip = aws.instance_private_ip(self.s.region, self.s.worker_id)
        if not ip:
            raise RuntimeError(f"worker {self.s.worker_id} has no private IP (is it running?)")
        key = aws.fetch_ssh_key(self.s.region, self.project.ssh_key_param)
        return SSHBackend(ip, key)

    @retry(retry=retry_if_exception_type(aws.CapacityError),
           wait=wait_fixed(20), stop=stop_after_attempt(3), reraise=True)
    def _start(self) -> None:
        aws.start_instance(self.s.region, self.s.worker_id)

    def prepare(self, sleep=time.sleep) -> None:
        region, wid = self.s.region, self.s.worker_id
        st = aws.instance_state(region, wid)
        if st in ("terminated", "shutting-down"):
            raise aws.UnrecoverableInstanceError(f"worker {wid} is {st}")
        if st == "stopping":
            aws.wait_state(region, wid, "stopped", 180)
        if aws.instance_state(region, wid) != "running":
            print(f"starting worker {wid} ...")
            try:
                self._start()
            except aws.CapacityError as e:
                raise aws.CapacityError(f"worker {wid}: no GPU capacity after retries — record a crash; retry next step") from e
            if not aws.wait_state(region, wid, "running", 240, sleep=sleep):
                raise RuntimeError(f"worker {wid} did not reach 'running' within budget")
        # Readiness = running AND the bootstrap marker exists (first start runs setup.sh).
        print("instance running; waiting for bootstrap marker (up to ~15 min) ...")
        for _ in range(90):
            if aws.instance_state(region, wid) != "running":
                self._start()
                aws.wait_state(region, wid, "running", 240, sleep=sleep)
                continue
            if self.backend.run_raw(f"test -f {_READY_MARKER}").ok:
                print(f"worker {wid} READY")
                return
            sleep(10)
        raise RuntimeError(f"worker {wid} NOT READY after timeout")

    def finalize(self) -> None:
        aws.stop_instance(self.s.region, self.s.worker_id)
        print(f"stop requested for worker {self.s.worker_id}")

    def _cost(self, run_seconds: int, uptime_seconds: int) -> tuple[float, int]:
        cost = cost_usd(uptime_seconds, self.project.worker_instance_type)
        return cost, int(uptime_seconds)
