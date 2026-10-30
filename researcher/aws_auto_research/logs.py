"""CloudWatch log shipping — replaces log-ship.sh.

Ship lines to a CloudWatch stream (+ optional local archive file), dropping the high-frequency
stream-json `thinking_tokens` noise. Best-effort: CloudWatch/file errors never abort the caller.
"""

from __future__ import annotations

import os
import time
from typing import Iterable

import boto3
from botocore.config import Config

_CFG = Config(retries={"mode": "adaptive", "max_attempts": 3})


def _drop(line: str) -> bool:
    return '"subtype":"thinking_tokens"' in line


class LogShipper:
    def __init__(self, region: str, log_group: str, log_stream: str,
                 log_file: str | None = None, echo: bool = True):
        self._logs = boto3.client("logs", region_name=region, config=_CFG)
        self.group = log_group
        self.stream = log_stream
        self.file = log_file
        self.echo = echo
        try:
            self._logs.create_log_stream(logGroupName=log_group, logStreamName=log_stream)
        except Exception:  # noqa: BLE001 — already-exists or transient
            pass
        if log_file:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

    def ship_line(self, line: str) -> None:
        if self.echo:
            print(line, flush=True)
        if _drop(line):
            return
        if self.file:
            try:
                with open(self.file, "a") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        try:
            self._logs.put_log_events(
                logGroupName=self.group,
                logStreamName=self.stream,
                logEvents=[{"timestamp": int(time.time() * 1000), "message": line[:250000] or " "}],
            )
        except Exception:  # noqa: BLE001
            pass

    def ship(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.ship_line(line.rstrip("\n"))


def send_log(region: str, log_group: str, log_stream: str, message: str) -> None:
    """One-shot log line (boot progress). Best-effort."""
    try:
        c = boto3.client("logs", region_name=region, config=_CFG)
        try:
            c.create_log_stream(logGroupName=log_group, logStreamName=log_stream)
        except Exception:  # noqa: BLE001
            pass
        c.put_log_events(logGroupName=log_group, logStreamName=log_stream,
                         logEvents=[{"timestamp": int(time.time() * 1000), "message": message or " "}])
    except Exception:  # noqa: BLE001
        pass
    print(message, flush=True)
