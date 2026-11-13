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


# --- hardening: the shipper must survive ANY input (it is the loop's only pipe reader) -----------
import io  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def shipper(tmp_path, mocker):
    mocker.patch.object(logs, "boto3")
    s = logs.LogShipper("us-east-1", "g", "s", str(tmp_path / "log.jsonl"), echo=False)
    s.put = s._logs.put_log_events
    return s


def _messages(s):
    return [c.kwargs["logEvents"][0]["message"] for c in s.put.call_args_list]


def test_invalid_utf8_is_replaced_and_warned(shipper, capsys):
    shipper.ship_stream(io.BytesIO(b'{"a":"ok"}\n{"b":"bad \xff\xfe byte"}\n{"c":"after"}\n'))
    msgs = _messages(shipper)
    assert msgs[0] == '{"a":"ok"}'
    assert any("[log-ship] WARNING (decode" in m for m in msgs)
    assert '{"b":"bad \ufffd\ufffd byte"}' in msgs
    assert msgs[-1] == '{"c":"after"}'           # kept going
    assert "WARNING (decode" in capsys.readouterr().err


def test_broken_echo_disables_echo_and_continues(shipper, mocker):
    shipper.echo = True
    mocker.patch.object(logs.os, "write", side_effect=BrokenPipeError(32, "Broken pipe"))
    shipper.ship_stream(io.BytesIO(b"one\ntwo\n"))
    assert shipper.echo is False
    msgs = _messages(shipper)
    assert "one" in msgs and "two" in msgs
    assert sum("WARNING (echo" in m for m in msgs) == 1


def test_echo_survives_nonblocking_pipe_with_line_over_64k(shipper, mocker):
    """The real 2026-11-12 crash: stdout was an O_NONBLOCK pipe and one stream-json line was bigger
    than the 64 KiB pipe buffer → BlockingIOError (EAGAIN) killed the shipper. Now it must wait for
    the reader and deliver the whole line, with echo still on and no warning."""
    import fcntl
    import threading
    r, w = os.pipe()
    fcntl.fcntl(w, fcntl.F_SETFL, fcntl.fcntl(w, fcntl.F_GETFL) | os.O_NONBLOCK)
    got = bytearray()

    def slow_reader():
        time.sleep(0.3)                             # pipe fills up first → EAGAIN on the writer
        while chunk := os.read(r, 65536):
            got.extend(chunk)
    t = threading.Thread(target=slow_reader)
    t.start()
    fake_sys = type("FakeSys", (), {"stdout": type("Out", (), {"fileno": lambda self: w})(),
                                     "stderr": logs.sys.stderr})()
    mocker.patch.object(logs, "sys", fake_sys)
    shipper.echo = True
    big = '{"type":"user","content":"' + "x" * 200_000 + '"}'   # > 64 KiB pipe, < CloudWatch cap
    shipper.ship_stream(io.BytesIO((big + "\nafter\n").encode()))
    os.close(w)
    t.join(5)
    os.close(r)
    lines = bytes(got).split(b"\n")
    assert big.encode() in lines and b"after" in lines
    assert shipper.echo is True and not shipper._warn_counts


def test_write_all_times_out_instead_of_hanging():
    import fcntl
    r, w = os.pipe()
    fcntl.fcntl(w, fcntl.F_SETFL, fcntl.fcntl(w, fcntl.F_GETFL) | os.O_NONBLOCK)
    assert logs.write_all(w, b"z" * 1_000_000, timeout_s=0.2) is False   # nobody reads
    os.close(r)
    os.close(w)


def test_oversized_line_truncated_by_bytes(shipper):
    big = "τ" * 200_000                            # 400 KB of UTF-8, only 200k chars
    shipper.ship_stream(io.BytesIO((big + "\n").encode()))
    sent = [m for m in _messages(shipper) if not m.startswith("[log-ship]")][0]
    assert len(sent.encode()) <= logs._CW_MAX_EVENT_BYTES
    assert sent.endswith("[truncated by log-ship]")
    assert any("WARNING (truncated" in m for m in _messages(shipper))


def test_put_failure_and_unexpected_errors_never_raise(shipper, mocker):
    shipper.put.side_effect = RuntimeError("throttled")
    mocker.patch.object(logs, "_drop", side_effect=[ValueError("weird"), False, False, False, False])
    shipper.ship_stream(io.BytesIO(b"l1\nl2\nl3\n"))  # must not raise
    assert shipper._warn_counts.get("ship_line") == 1
    assert shipper._warn_counts.get("put_log_events", 0) >= 1


def test_warnings_are_rate_limited(shipper):
    for _ in range(250):
        shipper.warn("x", "m")
    assert sum("WARNING (x" in m for m in _messages(shipper)) == 3   # 1st, 100th, 200th


def test_log_ship_cli_drains_if_shipper_cannot_start(mocker, monkeypatch, capsys):
    from aws_auto_research import cli
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("LOG_GROUP", raising=False)  # KeyError at construction → fallback path
    data = io.BytesIO(b"x" * 200_000)
    monkeypatch.setattr(cli.sys, "stdin", type("S", (), {"buffer": data})())
    cli.log_ship()
    assert data.tell() == 200_000                  # read to EOF: the writer never sees EPIPE
    err = capsys.readouterr().err
    assert "DRAINING" in err and "drained 200000 unshipped bytes" in err


def test_log_ship_cli_echo_off_by_default(mocker, monkeypatch):
    from aws_auto_research import cli
    for k, v in {"AWS_REGION": "us-east-1", "LOG_GROUP": "g", "LOG_STREAM": "s"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LOG_FILE", raising=False)
    monkeypatch.setattr(cli.sys, "stdin", type("S", (), {"buffer": io.BytesIO(b"a\n")})())
    made = mocker.patch.object(logs, "LogShipper")
    monkeypatch.delenv("LOG_ECHO", raising=False)
    cli.log_ship()
    assert made.call_args.kwargs["echo"] is False
    monkeypatch.setenv("LOG_ECHO", "1")
    cli.log_ship()
    assert made.call_args.kwargs["echo"] is True
