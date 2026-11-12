import json

import pytest

from aws_auto_research import costs
from aws_auto_research.costs import ClaudeCostMeter, bedrock_multiplier, budget_for_claude, prices

# Token counts of a real research step (superconductor project, eu-west-2). Claude Code reported
# total_cost_usd = 0.998431 at LIST price (costBasis "list") for exactly these tokens.
REAL_MODEL_USAGE = {
    "eu.anthropic.claude-opus-4-8": {
        "inputTokens": 48, "outputTokens": 10809, "cacheReadInputTokens": 850257,
        "cacheCreationInputTokens": 48454, "cacheCreationEphemeral5mInputTokens": 48454,
        "cacheCreationEphemeral1hInputTokens": 0, "webSearchRequests": 0,
        "costUSD": 0.998431, "costBasis": "list",
    }
}


@pytest.fixture(autouse=True)
def _no_multiplier_override(monkeypatch):
    monkeypatch.delenv("BEDROCK_PRICE_MULTIPLIER", raising=False)


def _assistant(mid, model="claude-opus-4-8", **usage):
    return json.dumps({"type": "assistant", "message": {"id": mid, "model": model, "usage": usage}})


def _result(model_usage):
    return json.dumps({"type": "result", "total_cost_usd": 0.0, "modelUsage": model_usage})


def test_list_price_matches_claude_code_exactly():
    mu = REAL_MODEL_USAGE["eu.anthropic.claude-opus-4-8"]
    assert costs.model_usage_cost("eu.anthropic.claude-opus-4-8", mu, multiplier=1.0) == pytest.approx(0.998431, abs=1e-9)


def test_geo_profile_premium():
    assert bedrock_multiplier("eu.anthropic.claude-opus-4-8") == pytest.approx(1.10)
    assert bedrock_multiplier("us.anthropic.claude-sonnet-4-5-20250929-v1:0") == pytest.approx(1.10)
    assert bedrock_multiplier("anthropic.claude-opus-4-8") == pytest.approx(1.10)       # in-region bare id
    assert bedrock_multiplier("global.anthropic.claude-opus-4-8") == 1.0
    assert bedrock_multiplier("us.anthropic.claude-opus-4-1-20250805-v1:0") == 1.0     # pre-4.5: one price


def test_multiplier_override(monkeypatch):
    monkeypatch.setenv("BEDROCK_PRICE_MULTIPLIER", "1.0")
    assert bedrock_multiplier("eu.anthropic.claude-opus-4-8") == 1.0


def test_model_families():
    assert prices("claude-opus-4-8")[:2] == (5.0, 25.0)
    assert prices("anthropic.claude-opus-4-1-20250805-v1:0")[:2] == (15.0, 75.0)
    assert prices("us.anthropic.claude-sonnet-4-5-20250929-v1:0")[:2] == (3.0, 15.0)
    assert prices("claude-haiku-4-5")[:2] == (1.0, 5.0)
    assert prices("anthropic.claude-3-5-haiku-20241022-v1:0")[:2] == (0.80, 4.0)


def test_result_event_is_authoritative_and_reported_cumulatively():
    sent = []
    m = ClaudeCostMeter(lambda usd: sent.append(usd) or True, model_id="eu.anthropic.claude-opus-4-8",
                        flush_every_s=0)
    m.feed(_result(REAL_MODEL_USAGE))
    assert m.total_usd == pytest.approx(0.998431 * 1.10, rel=1e-9)
    m.close()
    assert sent and sent[-1] == pytest.approx(round(0.998431 * 1.10, 6))
    assert sent == sorted(sent)            # cumulative, monotonic


def test_partial_stream_is_charged_when_killed_before_result():
    """A sub-agent killed by `timeout` never emits `result` — it must still be charged."""
    sent = []
    m = ClaudeCostMeter(lambda usd: sent.append(usd) or True, model_id="eu.anthropic.claude-opus-4-8",
                        flush_every_s=3600)
    # same message streamed twice (partial then final usage) → de-duplicated by id, max per field
    m.feed(_assistant("m1", input_tokens=10, output_tokens=5, cache_read_input_tokens=1000))
    m.feed(_assistant("m1", input_tokens=10, output_tokens=200, cache_read_input_tokens=1000))
    m.feed(_assistant("m2", input_tokens=1, output_tokens=100,
                      cache_creation_input_tokens=2000,
                      cache_creation={"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 0}))
    expected = (11 * 5 + 300 * 25 + 1000 * 0.5 + 2000 * 6.25) / 1e6 * 1.10
    assert m.total_usd == pytest.approx(expected)
    assert sent == []                      # throttled: nothing flushed yet
    m.close()                              # process ended (killed) → forced final flush
    assert sent[-1] == pytest.approx(expected, abs=1e-6)


def test_output_floored_by_emitted_content():
    """Streamed usage snapshots can under-report output_tokens; the emitted content is a floor."""
    m = ClaudeCostMeter(lambda usd: True, model_id="global.anthropic.claude-opus-4-8", flush_every_s=3600)
    text = "x" * 3500
    line = json.dumps({"type": "assistant", "message": {
        "id": "m1", "model": "claude-opus-4-8", "usage": {"input_tokens": 0, "output_tokens": 1},
        "content": [{"type": "text", "text": text}]}})
    m.feed(line)
    m.feed(line)                           # the same block re-emitted is not double counted
    est_tokens = len(json.dumps({"text": text, "type": "text"}, separators=(",", ":"))) / 3.5
    assert m.total_usd == pytest.approx(int(est_tokens) * 25 / 1e6)


def test_result_overrides_a_higher_interim_estimate():
    m = ClaudeCostMeter(lambda usd: True, model_id="global.anthropic.claude-opus-4-8", flush_every_s=3600)
    m.feed(_assistant("m1", output_tokens=1_000_000))       # interim: $25
    m.feed(_result({"global.anthropic.claude-opus-4-8": {"outputTokens": 100}}))
    assert m.total_usd == pytest.approx(100 * 25 / 1e6)     # exact usage wins


def test_failed_report_is_retried_with_cumulative_value():
    calls = []
    outcomes = iter([False, True])
    m = ClaudeCostMeter(lambda usd: calls.append(usd) or next(outcomes), flush_every_s=0)
    m.feed(_assistant("m1", input_tokens=1_000_000))
    m.feed(_assistant("m2", input_tokens=1_000_000))
    assert m.reported_usd == pytest.approx(calls[-1])
    assert calls[-1] >= calls[0]           # the retry carries the full cumulative amount


def test_report_exception_does_not_break_stream():
    def boom(_):
        raise RuntimeError("network")
    m = ClaudeCostMeter(boom, flush_every_s=0)
    m.feed(_assistant("m1", input_tokens=10))   # must not raise
    assert m.reported_usd == 0.0


def test_ignores_noise_and_garbage():
    m = ClaudeCostMeter(lambda usd: True, flush_every_s=0)
    for line in ["", "not json", '{"type":"assistant"', json.dumps({"type": "user"}), b"\xff\xfe"]:
        m.feed(line)
    assert m.total_usd == 0.0


def test_budget_for_claude():
    model = "eu.anthropic.claude-opus-4-8"
    assert budget_for_claude(None, 5.0, model) == 5.0                     # no project cap
    assert budget_for_claude(100.0, 5.0, model) == 5.0                    # configured cap binds
    assert budget_for_claude(1.10, 5.0, model) == pytest.approx(1.0)      # remaining $ → list $
    assert budget_for_claude(0.0, 5.0, model) == 0.0


def test_run_metered_relays_verbatim_and_meters(tmp_path):
    script = tmp_path / "fake_claude.sh"
    lines = [_assistant("m1", input_tokens=1000), _result(REAL_MODEL_USAGE)]
    script.write_text("#!/bin/bash\n" + "".join(f"echo '{ln}'\n" for ln in lines))
    script.chmod(0o755)
    got, sent = [], []
    m = ClaudeCostMeter(lambda usd: sent.append(usd) or True, model_id="eu.anthropic.claude-opus-4-8")
    rc = costs.run_metered([str(script)], {}, m, got.append)
    assert rc == 0
    assert [g.decode().rstrip("\n") for g in got] == lines
    assert sent[-1] == pytest.approx(round(0.998431 * 1.10, 6))
