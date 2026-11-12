import json

from aws_auto_research.result import RunResult


def test_shape_matches_contract():
    d = RunResult(
        experiment_id="e1", status="keep", metric="0.5", peak_vram_mb="1240",
        run_seconds=300, run_log_s3_uri="s3://b/e1/run.log", notes="n",
    ).to_dict()
    assert d["metric_value"] == "0.5"      # metric_value is a string (legacy contract)
    # No self-reported cost: the dispatcher meters cost and adds it to the returned result.
    assert set(d) == {"experiment_id", "metric_value", "peak_vram_mb", "status", "run_seconds",
                      "run_log_s3_uri", "notes"}


def test_null_metric_serializes_to_json_null():
    d = RunResult("e1", "crash", None, None, 0, "s3://b/e1/run.log").to_dict()
    assert d["metric_value"] is None and d["peak_vram_mb"] is None
    assert json.loads(RunResult("e1", "crash", None, None, 0, "x").to_json())["metric_value"] is None
