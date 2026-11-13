"""CloudWatch log shipping — replaces log-ship.sh.

Ship lines to a CloudWatch stream (+ optional local archive file), dropping the high-frequency
stream-json `thinking_tokens` noise. Best-effort: CloudWatch/file errors never abort the caller.

The `aws-research log-ship` process is the ONLY reader of the research loop's stdout pipe (see
researcher/boot.py). If it dies, the loop's next write raises BrokenPipeError and the loop dies too —
silently, because its traceback goes into the same dead pipe. So `ship_stream` must never raise on
any input: every per-line failure (undecodable bytes, a broken echo stdout, a CloudWatch rejection,
anything else) is caught, reported as a `[log-ship] WARNING` line, and shipping continues.
"""

from __future__ import annotations

import os
import select
import sys
import time
import traceback
from typing import BinaryIO, Iterable

import boto3
from botocore.config import Config

_CFG = Config(retries={"mode": "adaptive", "max_attempts": 3})
# CloudWatch caps one event at 256 KiB INCLUDING 26 bytes of overhead — measured in UTF-8 BYTES, not
# characters (a line of 250k non-ASCII chars can be ~750 KB and would be rejected outright).
_CW_MAX_EVENT_BYTES = 256 * 1024 - 26
_TRUNC_MARK = " …[truncated by log-ship]"
_WARN_EVERY = 100   # after the first warning of a kind, report only every Nth repeat


def _drop(line: str) -> bool:
    return '"subtype":"thinking_tokens"' in line


def write_all(fd: int, data: bytes, timeout_s: float = 10.0) -> bool:
    """Write ALL of `data` to `fd`, tolerating a NON-BLOCKING pipe.

    The shipper's stdout is boot.sh's `>(tee …)` pipe, whose open file description is shared with
    other processes; any of them (e.g. Node's `claude`) may switch it to O_NONBLOCK. Then a write
    larger than the pipe's free space (64 KiB buffer — one big stream-json line, e.g. a Read tool
    result) fails with EAGAIN, which Python surfaces as BlockingIOError. That exact error killed the
    shipper (and with it the research loop) on 2026-11-12. So: write what fits, wait until the fd is
    writable, continue. Returns False if not done within `timeout_s` (the rest is dropped)."""
    view = memoryview(data)
    deadline = time.monotonic() + timeout_s
    while view:
        try:
            view = view[os.write(fd, view):]
        except BlockingIOError:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            select.select([], [fd], [], min(left, 0.5))
        except InterruptedError:
            continue
    return True


def _truncate_utf8(s: str, max_bytes: int) -> tuple[str, bool]:
    """Cut `s` to at most `max_bytes` UTF-8 bytes on a character boundary (+ a marker)."""
    b = s.encode("utf-8", "replace")
    if len(b) <= max_bytes:
        return s, False
    keep = max_bytes - len(_TRUNC_MARK.encode("utf-8"))
    return b[:keep].decode("utf-8", "ignore") + _TRUNC_MARK, True


class LogShipper:
    def __init__(self, region: str, log_group: str, log_stream: str,
                 log_file: str | None = None, echo: bool = True):
        self._logs = boto3.client("logs", region_name=region, config=_CFG)
        self.group = log_group
        self.stream = log_stream
        self.file = log_file
        self.echo = echo
        self._warn_counts: dict[str, int] = {}
        try:
            self._logs.create_log_stream(logGroupName=log_group, logStreamName=log_stream)
        except Exception:  # noqa: BLE001 — already-exists or transient
            pass
        if log_file:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # --- diagnostics -----------------------------------------------------------------
    def warn(self, kind: str, msg: str) -> None:
        """Report that a safeguard fired. Rate-limited per `kind`; never raises.

        Goes to stderr (boot's tee → /var/log/ar-boot.log → the CloudWatch `boot` stream) AND, best
        effort, to this shipper's own stream so it shows up next to the research log in the web app."""
        n = self._warn_counts.get(kind, 0) + 1
        self._warn_counts[kind] = n
        if n > 1 and n % _WARN_EVERY:
            return
        line = f"[log-ship] WARNING ({kind}, occurrence {n}): {msg}"
        try:
            print(line, file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001
            pass
        self._put(line, report=False)

    def _put(self, line: str, report: bool = True) -> None:
        msg, cut = _truncate_utf8(line, _CW_MAX_EVENT_BYTES)
        if cut and report:
            self.warn("truncated", f"line of {len(line.encode('utf-8', 'replace'))} bytes truncated to "
                                   f"the CloudWatch event limit ({_CW_MAX_EVENT_BYTES} B)")
        try:
            self._logs.put_log_events(
                logGroupName=self.group,
                logStreamName=self.stream,
                logEvents=[{"timestamp": int(time.time() * 1000), "message": msg or " "}],
            )
        except Exception as e:  # noqa: BLE001
            if report:
                self.warn("put_log_events", f"CloudWatch rejected a line ({len(msg)} chars): "
                                            f"{type(e).__name__}: {str(e)[:300]}")

    # --- shipping --------------------------------------------------------------------
    def ship_line(self, line: str) -> None:
        if self.echo:
            try:
                if not write_all(sys.stdout.fileno(), (line + "\n").encode("utf-8", "replace")):
                    self.warn("echo_timeout", f"stdout echo of a {len(line)}-char line stalled >10s "
                                              f"(non-blocking pipe full); rest of that line skipped "
                                              f"in the boot log only — CloudWatch shipping continues")
            except Exception as e:  # noqa: BLE001 — e.g. boot's tee died: keep shipping without echo
                self.echo = False
                self.warn("echo", f"stdout echo failed ({type(e).__name__}: {e}); echo disabled, "
                                  f"CloudWatch shipping continues")
        if _drop(line):
            return
        if self.file:
            try:
                with open(self.file, "a", encoding="utf-8", errors="replace") as f:
                    f.write(line + "\n")
            except OSError as e:
                self.warn("archive", f"local archive write to {self.file} failed: {e}")
        self._put(line)

    def _ship_guarded(self, line: str) -> None:
        try:
            self.ship_line(line)
        except Exception as e:  # noqa: BLE001 — a single bad line must never kill the shipper
            self.warn("ship_line", f"unexpected {type(e).__name__} shipping a {len(line)}-char line "
                                   f"(skipped): {e} | {traceback.format_exc(limit=3)[-600:]}")

    def ship(self, lines: Iterable[str]) -> None:
        for line in lines:
            self._ship_guarded(line.rstrip("\n"))

    def ship_stream(self, stream: BinaryIO) -> None:
        """Ship a BYTE stream line by line until EOF. Decoding happens here (not in a strict text
        wrapper) so undecodable bytes become U+FFFD instead of a fatal UnicodeDecodeError.
        readline() (not iteration) so each line ships the moment it arrives."""
        for raw in iter(stream.readline, b""):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as e:
                line = raw.decode("utf-8", "replace")
                self.warn("decode", f"invalid UTF-8 in a {len(raw)}-byte line ({e}); "
                                    f"shipped with U+FFFD replacements")
            self._ship_guarded(line.rstrip("\r\n"))


def drain(stream: BinaryIO) -> int:
    """Read a stream to EOF, discarding it — keeps the writer from getting EPIPE. Returns bytes read."""
    n = 0
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return n
            n += len(chunk)
    except Exception:  # noqa: BLE001
        return n


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
    try:
        print(message, flush=True)
    except Exception:  # noqa: BLE001
        pass
