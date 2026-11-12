"""Claude invocation cost metering, from `claude -p --output-format stream-json` output.

Every `claude -p` the runtime spawns (research step, experiment sub-agent, paper) is streamed through
a ClaudeCostMeter, which prices the TOKENS it sees and reports the invocation's CUMULATIVE USD to the
control plane (agent_api `meter_claude`, which charges only the increase — retry-safe):

- During the run, cost is estimated from the per-message `usage` of `assistant` events (de-duplicated
  by message id; streamed output_tokens can be partial, so output is floored by the size of the
  content blocks actually emitted). This means a step that is killed (timeout, Stop button, crash)
  is still charged for what it consumed so far.
- On each `result` event, cost is recomputed from the cumulative `modelUsage` — exact token counts for
  the whole invocation, including in-process Task sub-agents.

We price tokens ourselves instead of trusting `total_cost_usd`: Claude Code reports Anthropic LIST
prices (`costBasis: "list"`), which omit Bedrock's ~10% premium for geographic / in-region inference
profiles (`us.` / `eu.` / `apac.` / bare ids) over `global.` profiles.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Callable

# USD per 1M tokens: (input, output, cache_write_5m, cache_write_1h, cache_read)
_OPUS_NEW = (5.0, 25.0, 6.25, 10.0, 0.50)     # Opus 4.5+
_OPUS_OLD = (15.0, 75.0, 18.75, 30.0, 1.50)   # Opus 4 / 4.1
_SONNET_4 = (3.0, 15.0, 3.75, 6.0, 0.30)      # Sonnet 4.x
_HAIKU_45 = (1.0, 5.0, 1.25, 2.0, 0.10)       # Haiku 4.5
_HAIKU_35 = (0.80, 4.0, 1.0, 1.6, 0.08)       # Haiku 3.5
_UNKNOWN = _OPUS_NEW
WEB_SEARCH_USD = 0.01                          # per server-side web search request
GEO_PREMIUM = 1.10                             # geographic/in-region vs global inference profile
MIN_CLAUDE_BUDGET_USD = 0.05                   # below this, don't start a claude invocation at all
# Streamed `usage.output_tokens` can be a partial snapshot; as a floor, estimate output tokens from
# the size of the content blocks actually emitted. Calibrated on 38 real invocations: without the
# floor the interim estimate is 66-80% of the final cost; at 3.5 chars/token it is 93-97% and never
# above it (3.0 overshoots by up to 2%, which would be charged irreversibly).
_CHARS_PER_OUTPUT_TOKEN = 3.5

_MODEL_RE = re.compile(r"claude-(opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?(?!\d)")
_MODEL_RE_OLD = re.compile(r"claude-(\d)-(\d)-(opus|sonnet|haiku)")
_COST_EVENT_RE = re.compile(r'"type"\s*:\s*"(?:assistant|result)"')


def _family(model: str) -> tuple[str, int, int]:
    m = _MODEL_RE.search(model or "")
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3) or 0)
    m = _MODEL_RE_OLD.search(model or "")
    if m:
        return m.group(3), int(m.group(1)), int(m.group(2))
    return "", 0, 0


def prices(model: str) -> tuple[float, float, float, float, float]:
    fam, major, minor = _family(model)
    if fam == "opus":
        return _OPUS_OLD if (major, minor) < (4, 5) else _OPUS_NEW
    if fam == "sonnet":
        return _SONNET_4
    if fam == "haiku":
        return _HAIKU_35 if major < 4 else _HAIKU_45
    return _UNKNOWN


def bedrock_multiplier(model_id: str) -> float:
    """Price multiplier for the inference profile actually invoked. BEDROCK_PRICE_MULTIPLIER overrides."""
    override = os.environ.get("BEDROCK_PRICE_MULTIPLIER")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    if (model_id or "").startswith("global."):
        return 1.0
    fam, major, minor = _family(model_id)
    # The global/regional price split exists for the 4.5+ generation; older models have one price.
    return GEO_PREMIUM if fam and (major, minor) >= (4, 5) else 1.0


def tokens_cost(model: str, inp: int, out: int, cw5m: int, cw1h: int, cr: int, web: int = 0) -> float:
    p_in, p_out, p_cw5, p_cw1, p_cr = prices(model)
    usd = (inp * p_in + out * p_out + cw5m * p_cw5 + cw1h * p_cw1 + cr * p_cr) / 1_000_000
    return usd + web * WEB_SEARCH_USD


def _i(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def model_usage_cost(model_id: str, mu: dict, multiplier: float | None = None) -> float:
    """Cost of one `result.modelUsage[model_id]` entry."""
    cw = _i(mu.get("cacheCreationInputTokens"))
    cw5 = mu.get("cacheCreationEphemeral5mInputTokens")
    cw1 = _i(mu.get("cacheCreationEphemeral1hInputTokens"))
    cw5 = _i(cw5) if cw5 is not None else max(0, cw - cw1)
    base = tokens_cost(model_id, _i(mu.get("inputTokens")), _i(mu.get("outputTokens")),
                       cw5, cw1, _i(mu.get("cacheReadInputTokens")), _i(mu.get("webSearchRequests")))
    return base * (bedrock_multiplier(model_id) if multiplier is None else multiplier)


def _usage_tokens(u: dict) -> dict:
    cc = u.get("cache_creation") or {}
    cw = _i(u.get("cache_creation_input_tokens"))
    cw1 = _i(cc.get("ephemeral_1h_input_tokens"))
    cw5 = _i(cc.get("ephemeral_5m_input_tokens")) if "ephemeral_5m_input_tokens" in cc else max(0, cw - cw1)
    web = _i((u.get("server_tool_use") or {}).get("web_search_requests"))
    return {"in": _i(u.get("input_tokens")), "out": _i(u.get("output_tokens")),
            "cw5": cw5, "cw1": cw1, "cr": _i(u.get("cache_read_input_tokens")), "web": web}


class ClaudeCostMeter:
    """Accumulates one claude invocation's cost and reports the cumulative USD via `report`.

    `report(cumulative_usd) -> bool` must be idempotent on cumulative values (agent_api meter_claude
    is). Thread-safe: `feed` runs on the stream thread, `flush` also on a background ticker.
    """

    def __init__(self, report: Callable[[float], bool], model_id: str = "",
                 flush_every_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._report = report
        self._model_id = model_id
        self._mult = bedrock_multiplier(model_id)
        self._flush_every = flush_every_s
        self._clock = clock
        self._lock = threading.Lock()
        self._msgs: dict[str, tuple[str, dict]] = {}
        self._blocks: dict[str, dict[int, int]] = {}
        self._final: float | None = None
        self._reported = 0.0
        self._last_flush = clock()
        self._ticker: threading.Thread | None = None
        self._stop = threading.Event()

    # --- accounting ----------------------------------------------------------------
    def _estimate(self) -> float:
        total = 0.0
        for model, t in self._msgs.values():
            total += tokens_cost(model or self._model_id, t["in"], t["out"], t["cw5"], t["cw1"],
                                 t["cr"], t["web"]) * self._mult
        return total

    @property
    def total_usd(self) -> float:
        with self._lock:
            if self._final is not None:
                # The cumulative `result` usage is exact; never let the interim estimate override it.
                return self._final
            return self._estimate()

    @property
    def reported_usd(self) -> float:
        return self._reported

    def feed(self, line: str | bytes) -> None:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        if not _COST_EVENT_RE.search(line):
            return  # cheap pre-filter: most lines are tool output / partial deltas
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(obj, dict):
            return
        with self._lock:
            if obj.get("type") == "assistant":
                msg = obj.get("message") or {}
                usage, mid = msg.get("usage"), msg.get("id")
                if isinstance(usage, dict) and mid:
                    new = _usage_tokens(usage)
                    old = self._msgs.get(mid, ("", {}))[1]
                    merged = {k: max(v, old.get(k, 0)) for k, v in new.items()}
                    # Floor for output tokens: the content blocks emitted so far for this message.
                    blocks = self._blocks.setdefault(mid, {})
                    for b in msg.get("content") or []:
                        s = json.dumps(b, separators=(",", ":"), sort_keys=True)
                        blocks[hash(s)] = len(s)
                    est_out = int(sum(blocks.values()) / _CHARS_PER_OUTPUT_TOKEN)
                    merged["out"] = max(merged["out"], est_out)
                    self._msgs[mid] = (msg.get("model") or "", merged)
            elif obj.get("type") == "result":
                mu = obj.get("modelUsage")
                if isinstance(mu, dict) and mu:
                    cost = sum(model_usage_cost(m, u) for m, u in mu.items() if isinstance(u, dict))
                    self._final = max(self._final or 0.0, cost)
        self.flush()

    # --- reporting -----------------------------------------------------------------
    def flush(self, force: bool = False) -> bool:
        total = self.total_usd
        with self._lock:
            if total - self._reported < 1e-6:
                return True
            if not force and self._clock() - self._last_flush < self._flush_every:
                return True
            self._last_flush = self._clock()
        try:
            ok = bool(self._report(round(total, 6)))
        except Exception:  # noqa: BLE001 — never break the stream on a metering error
            ok = False
        if ok:
            with self._lock:
                self._reported = max(self._reported, total)
        return ok

    def start_ticker(self) -> "ClaudeCostMeter":
        """Flush periodically even while the stream is silent (long tool calls)."""
        def _run():
            while not self._stop.wait(self._flush_every):
                self.flush()
        self._ticker = threading.Thread(target=_run, daemon=True)
        self._ticker.start()
        return self

    def close(self, attempts: int = 3) -> float:
        self._stop.set()
        for i in range(attempts):
            if self.flush(force=True):
                break
            time.sleep(2 * (i + 1))
        return self.total_usd


def claude_reporter(client, project_id: str, invocation_id: str,
                    experiment_id: str | None = None) -> Callable[[float], bool]:
    """A ClaudeCostMeter `report` callable bound to agent_api meter_claude."""
    def _report(cumulative: float) -> bool:
        r = client.meter_claude(project_id, invocation_id, cumulative, experiment_id)
        if not r.get("ok"):
            print(f"[cost] meter_claude failed for {invocation_id}: {r.get('error')}", flush=True)
        return bool(r.get("ok"))
    return _report


def budget_for_claude(remaining_usd: float | None, cap_usd: float, model_id: str) -> float:
    """`--max-budget-usd` for one invocation: the configured cap, bounded by the project's remaining
    budget. Claude Code enforces it at LIST prices, so convert real dollars back to list dollars.
    Callers must not start an invocation when this is below MIN_CLAUDE_BUDGET_USD."""
    cap = max(0.0, float(cap_usd))
    if remaining_usd is not None:
        cap = min(cap, max(0.0, remaining_usd) / bedrock_multiplier(model_id))
    return round(cap, 4)


def run_metered(argv: list[str], env: dict, meter: ClaudeCostMeter,
                on_line: Callable[[bytes], None]) -> int:
    """Run a stream-json `claude -p` child, handing every output line to `on_line` (verbatim bytes)
    and to the meter. The meter is flushed periodically and on exit — including when the child is
    killed — so a partial invocation is still charged."""
    import subprocess
    p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert p.stdout is not None
    meter.start_ticker()
    try:
        for raw in iter(p.stdout.readline, b""):
            on_line(raw)
            meter.feed(raw)
        return p.wait()
    finally:
        meter.close()
