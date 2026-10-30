from aws_auto_research import logs
from aws_auto_research.logs import _drop


def test_drop_thinking_tokens():
    assert _drop('{"type":"x","subtype":"thinking_tokens","n":5}')
    assert not _drop('{"type":"assistant","text":"hi"}')


def test_ship_writes_file_and_filters(tmp_path, mocker):
    mocker.patch.object(logs, "boto3")  # no real CloudWatch
    lf = tmp_path / "log.jsonl"
    shipper = logs.LogShipper("us-east-1", "g", "s", str(lf), echo=False)
    shipper.ship([
        '{"type":"assistant","text":"kept-1"}',
        '{"subtype":"thinking_tokens","n":9}',   # dropped from the archive
        '{"type":"result","text":"kept-2"}',
    ])
    body = lf.read_text().splitlines()
    assert body == ['{"type":"assistant","text":"kept-1"}', '{"type":"result","text":"kept-2"}']
