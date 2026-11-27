"""create_project: the auto-research.json `compute` defaults, CPU local researchers and required extensions."""

import json
from unittest import mock

from test_large_upload import _event, _load


def test_compute_comes_from_the_form_then_the_project_then_the_defaults():
    h = _load()
    cfg = {"compute": {"experiment_runner": "local", "researcher_instance_type": "m8g.large"}}
    assert h._compute({}, cfg) == ("local", "m8g.large", "g4dn.xlarge")
    assert h._compute({"experiment_runner": "ec2", "worker_instance_type": "g5.xlarge"}, cfg) == ("ec2", "m8g.large", "g5.xlarge")
    assert h._compute({}, {}) == ("ec2", "m8g.large", "g4dn.xlarge")
    assert h._compute({"experiment_runner": "bogus"}, {})[0] == "ec2"


def test_only_gpu_families_get_the_deep_learning_ami():
    h = _load()
    assert [h._is_gpu(t) for t in ("g4dn.xlarge", "g6e.2xlarge", "p5.48xlarge", "m8g.large", "c7g.xlarge", "m7i.large")] \
        == [True, True, True, False, False, False]


def test_a_required_extension_that_is_not_installed_refuses_the_project():
    h = _load()
    files = [{"path": "auto-research.json", "content": json.dumps({"extensions": ["robots-sim"]})}]
    with mock.patch.object(h, "s3") as s3, mock.patch.object(h, "dynamodb") as ddb:
        s3.get_object.side_effect = Exception("NoSuchKey")
        ddb.Table.return_value.get_item.return_value = {}
        r = h.handler(_event("/projects", body={"project_name": "p", "files": files, "budget_usd": 5}), None)
    assert r["statusCode"] == 400 and "robots-sim" in json.loads(r["body"])["error"]


def test_claude_md_names_the_extensions_skills():
    h = _load()
    md = h._claude_md({"extensions": ["robots-sim"], "metric_name": "success_rate"}, "brief")
    assert "`/robots-sim`" in md and "budget" in md
    assert "Domain extensions" not in h._claude_md({}, "brief")
