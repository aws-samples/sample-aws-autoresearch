import json

from aws_auto_research.result import RunResult


def test_shape_matches_contract():
    d = RunResult(
        experiment_id="e1", status="keep", metric="0.5", peak_vram_mb="1240",
        run_seconds=300, worker_uptime_seconds=360, cost_usd=0.0526,
        run_log_s3_uri="s3://b/e1/run.log", notes="n",
    ).to_dict()
    assert d["metric_value"] == "0.5"      # metric_value is a string (legacy contract)
    assert d["cost_usd"] == "0.0526"       # 4dp string
    assert d["worker_uptime_seconds"] == 360
    assert set(d) == {"experiment_id", "metric_value", "peak_vram_mb", "status", "run_seconds",
                      "worker_uptime_seconds", "cost_usd", "run_log_s3_uri", "notes"}


def test_null_metric_serializes_to_json_null():
    d = RunResult("e1", "crash", None, None, 0, 0, 0.0, "s3://b/e1/run.log").to_dict()
    assert d["metric_value"] is None and d["peak_vram_mb"] is None
    assert json.loads(RunResult("e1", "crash", None, None, 0, 0, 0.0, "x").to_json())["metric_value"] is None
