"""Runtime settings resolved from env vars (+ IMDSv2 for region as a fallback).

The dispatcher/loop preset PROJECT_ID, AWS_REGION, EXP_ID, BRANCH, WORKER_ID, RESULT_FILE — the same
env contract the shell scripts read. IMDSv2 region resolution uses the stable raw two-step (PUT token
→ GET metadata), not botocore's internal IMDSFetcher.
"""

from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass

_IMDS = "http://169.254.169.254/latest"


def imds_region(timeout: float = 1.0) -> str | None:
    """Best-effort EC2 region via IMDSv2; None off-instance or on error."""
    try:
        tok = urllib.request.urlopen(
            urllib.request.Request(
                f"{_IMDS}/api/token",
                method="PUT",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            ),
            timeout=timeout,
        ).read().decode()
        return urllib.request.urlopen(
            urllib.request.Request(
                f"{_IMDS}/meta-data/placement/region",
                headers={"X-aws-ec2-metadata-token": tok},
            ),
            timeout=timeout,
        ).read().decode().strip()
    except Exception:  # noqa: BLE001 — any failure means "not on EC2 / unavailable"
        return None


@dataclass
class Settings:
    region: str
    project_id: str
    exp_id: str = ""
    branch: str = ""
    worker_id: str = ""
    result_file: str = ""
    code_dir: str = ""

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "Settings":
        env = environ if environ is not None else os.environ
        region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or imds_region() or "us-east-1"
        home = os.path.expanduser("~")
        return cls(
            region=region,
            project_id=env.get("PROJECT_ID", ""),
            exp_id=env.get("EXP_ID", ""),
            branch=env.get("BRANCH", ""),
            worker_id=env.get("WORKER_ID", ""),
            result_file=env.get("RESULT_FILE", ""),
            code_dir=env.get("CODE_DIR", os.path.join(home, "workspace", "code")),
        )
