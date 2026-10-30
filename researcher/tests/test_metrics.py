import pytest

from aws_auto_research.metrics import MetricsError, parse_metrics


def test_valid_full():
    m = parse_metrics('{"metric": 0.0041, "peak_vram_mb": 1240.5, "status": "ok", "notes": "hi"}')
    assert m.metric == 0.0041 and m.peak_vram_mb == 1240.5 and m.status == "ok"


def test_valid_minimal_defaults():
    m = parse_metrics('{"metric": 3}')
    assert m.metric == 3.0 and m.peak_vram_mb is None and m.status == "ok"


def test_invalid_status_penalty():
    m = parse_metrics('{"metric": 999, "status": "invalid", "notes": "constraint"}')
    assert m.status == "invalid" and m.metric == 999


@pytest.mark.parametrize("bad", ["", "   ", None, "not json", '{"foo": 1}', '[1,2]'])
def test_bad_inputs_raise(bad):
    with pytest.raises(MetricsError):
        parse_metrics(bad)
