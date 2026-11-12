"""Measure how long a GPU worker was RUNNING during one experiment — from EC2, not from the agent.

Used for per-experiment ATTRIBUTION (the experiment's worker_cost_usd / worker_uptime_seconds). The
project's billed worker total is metered independently by instance-meter.sh on the worker itself.

A background poller samples (state, LaunchTime). A running period starts at the instance's LaunchTime
(EC2 resets it on every start), clamped to when this experiment began; it ends at the first sample
that is no longer `running`. EC2 bills at least 60 s per start, which is applied per period.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

MIN_BILLED_S = 60


class WorkerUsage:
    def __init__(self, describe: Callable[[], tuple[str, float | None]], poll_s: float = 10.0,
                 clock: Callable[[], float] = time.time):
        self._describe = describe
        self._poll = poll_s
        self._clock = clock
        self._t0 = clock()
        self._open: float | None = None
        self._closed: list[float] = []
        self._state = "unknown"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample(self) -> str:
        state, launch = self._describe()
        now = self._clock()
        with self._lock:
            self._state = state
            if state == "running":
                if self._open is None:
                    start = launch if launch is not None else now
                    self._open = min(now, max(start, self._t0))
            elif state != "unknown" and self._open is not None:
                self._closed.append(max(0.0, now - self._open))
                self._open = None
        return state

    def seconds(self) -> int:
        with self._lock:
            periods = list(self._closed)
            if self._open is not None:
                periods.append(max(0.0, self._clock() - self._open))
        return int(sum(max(MIN_BILLED_S, p) for p in periods if p > 0))

    @property
    def state(self) -> str:
        return self._state

    def start(self) -> "WorkerUsage":
        def _run():
            while not self._stop.is_set():
                try:
                    self.sample()
                except Exception:  # noqa: BLE001 — a failed describe must not kill the poller
                    pass
                self._stop.wait(self._poll)
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return self

    def wait_until_down(self, timeout_s: float = 240.0, poll_s: float = 5.0,
                        sleep: Callable[[float], None] = time.sleep) -> bool:
        """After the stop request: keep sampling until the worker has left `running`/`pending`."""
        deadline = self._clock() + timeout_s
        while True:
            st = self.sample()
            if st not in ("running", "pending", "unknown"):
                return True
            if self._clock() >= deadline:
                return False
            sleep(poll_s)

    def stop(self) -> int:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._poll + 5)
        return self.seconds()
