"""Single source of truth for instance hourly rates + cost math.

Estimates for the budget guard, not billing. Was duplicated across ec2/local write-result.sh and
researcher-loop.sh; now one place.
"""

from __future__ import annotations

# Approximate on-demand $/hour. CPU types = ec2-mode researcher; GPU types = workers / local researcher.
_RATES: dict[str, float] = {
    # CPU (ec2-mode researcher)
    "m8g.large": 0.0898,
    "m9g.large": 0.1000,
    "m7i.large": 0.1008,
    "m7i.xlarge": 0.2016,
    "t3.large": 0.0832,
    # GPU (workers / local-mode researcher)
    "g4dn.xlarge": 0.526,
    "g5.xlarge": 1.006,
    "g6.xlarge": 0.8048,
    "p5.4xlarge": 6.881,
}

FALLBACK_HOURLY_USD = 1.00  # unknown GPU type → conservative


def hourly_usd(instance_type: str | None, fallback: float = FALLBACK_HOURLY_USD) -> float:
    return _RATES.get(instance_type or "", fallback)


def cost_usd(seconds: float, instance_type: str | None, fallback: float = FALLBACK_HOURLY_USD) -> float:
    """GPU cost estimate = hours × rate, rounded to 4 dp (never negative)."""
    hrs = max(0.0, float(seconds)) / 3600.0
    return round(hrs * hourly_usd(instance_type, fallback), 4)
