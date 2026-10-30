"""The metrics.json result contract.

evaluation_command MUST write metrics.json with at least {"metric": <number>}, optionally
peak_vram_mb / status / notes / metrics. There is no fallback — an absent/invalid file is a crash.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Metrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    metric: float
    peak_vram_mb: float | None = None
    status: str = "ok"  # "ok" | "invalid" (constraint violated → metric is a penalty)
    notes: str | None = None
    metrics: dict | None = None


class MetricsError(ValueError):
    """metrics.json was missing or did not satisfy the contract."""


def parse_metrics(text: str | None) -> Metrics:
    """Parse+validate metrics.json text. Raises MetricsError on any problem."""
    if not text or not text.strip():
        raise MetricsError("no metrics.json produced")
    try:
        return Metrics.model_validate_json(text)
    except Exception as e:  # noqa: BLE001 — normalize pydantic/JSON errors to one type
        raise MetricsError(f"invalid metrics.json: {e}") from e
