"""LocalRunner — experiments run on the researcher's own GPU. No worker, no SSH, cost billed via the loop."""

from __future__ import annotations

import os
import subprocess

from ..backends import Backend, LocalBackend
from .base import ExperimentRunner

_PREPARED_MARKER = os.path.expanduser("~/.local-experiment-prepared")


class LocalRunner(ExperimentRunner):
    name = "local"
    supports_interactive_debug = True   # there is a live shell (this box)
    incurs_gpu_cost = False             # billed once per step via researcher runtime

    def _make_backend(self) -> Backend:
        return LocalBackend(self.s.code_dir)

    def prepare(self) -> None:
        """One-time local setup via the project's setup.sh (deps + data). Idempotent."""
        if os.path.exists(_PREPARED_MARKER):
            print("local environment already prepared")
            return
        setup = os.path.join(self.s.code_dir, "setup.sh")
        if os.path.exists(setup):
            print("=== running setup.sh (one-time local prepare) ===")
            p = subprocess.run(["bash", "setup.sh"], cwd=self.s.code_dir)
            if p.returncode != 0:
                raise RuntimeError("setup.sh failed")
        open(_PREPARED_MARKER, "w").close()
        print("local prepare complete")

    def finalize(self) -> None:
        pass  # the researcher stays up; nothing to stop

    def _cost(self, run_seconds: int, uptime_seconds: int) -> tuple[float, int]:
        # No extra cost on top of the researcher; the GPU is billed per step by researcher-loop.
        return 0.0, 0
