"""RunResult — the JSON a runner writes to $RESULT_FILE, read by the dispatcher + finalize step.

Field shape matches the legacy write-result.sh contract exactly (metric_value / cost_usd as strings,
worker_uptime_seconds present) so the main agent's keep/merge logic is unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


def _as_str(v) -> str | None:
    return None if v is None else str(v)


@dataclass
class RunResult:
    experiment_id: str
    status: str  # keep | discard | crash
    metric: float | str | None
    peak_vram_mb: float | str | None
    run_seconds: int
    worker_uptime_seconds: int
    cost_usd: float
    run_log_s3_uri: str
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "metric_value": _as_str(self.metric),
            "peak_vram_mb": _as_str(self.peak_vram_mb),
            "status": self.status,
            "run_seconds": int(self.run_seconds),
            "worker_uptime_seconds": int(self.worker_uptime_seconds),
            "cost_usd": f"{float(self.cost_usd):.4f}",
            "run_log_s3_uri": self.run_log_s3_uri,
            "notes": self.notes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
