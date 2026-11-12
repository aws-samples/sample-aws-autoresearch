"""LocalRunner — experiments run on the researcher's own GPU. No worker, no SSH; the GPU's time is
metered as researcher cost by instance-meter.sh."""

from __future__ import annotations

import os
import subprocess

from ..backends import Backend, LocalBackend
from .base import ExperimentRunner

_PREPARED_MARKER = os.path.expanduser("~/.local-experiment-prepared")


class LocalRunner(ExperimentRunner):
    name = "local"
    supports_interactive_debug = True   # there is a live shell (this box)
    incurs_gpu_cost = False             # its GPU time is the researcher's (metered per instance)

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
        pass  # the researcher stays up; nothing to stop (its GPU time is metered by instance-meter.sh)
