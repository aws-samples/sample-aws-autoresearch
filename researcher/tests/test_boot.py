"""Loop supervision in researcher/boot.py: a crashed loop must not leave a stale 'active' project."""

from aws_auto_research.researcher import boot


def _sup(results, status="active", pause_ok=True):
    runs = iter(results)
    logs, sleeps, paused = [], [], []

    def set_paused():
        paused.append(True)
        return pause_ok
    out = boot.supervise(lambda: next(runs), lambda: status, set_paused, logs.append, sleeps.append)
    return out, logs, sleeps, paused


def test_clean_exit_no_restart():
    out, logs, sleeps, paused = _sup([(0, 0, 3600)])
    assert out == "clean" and not sleeps and not paused and logs == []


def test_crash_is_logged_and_restarted_then_clean():
    out, logs, sleeps, paused = _sup([(120, 1, 8000), (0, 0, 100)])
    assert out == "clean" and sleeps == [30] and not paused
    text = "\n".join(logs)
    assert "log shipper exited rc=1" in text
    assert "CRASHED (rc=120: the loop could not flush stdout" in text
    assert "restarting the research loop in 30s" in text
    assert "exited cleanly on run 2" in text


def test_repeated_fast_crashes_give_up_and_pause():
    out, logs, sleeps, paused = _sup([(1, 0, 5)] * 5)
    assert out == "gave-up" and paused == [True]
    assert sleeps == [30, 60]                      # 2 restarts, the 3rd fast crash gives up
    assert "giving up; status set to 'paused' OK" in logs[-1]


def test_slow_crashes_reset_the_counter():
    out, _, sleeps, paused = _sup([(1, 0, 5), (1, 0, 5000), (1, 0, 5), (0, 0, 1)])
    assert out == "clean" and not paused and sleeps == [30, 30, 60]


def test_crash_after_stop_button_does_not_restart():
    out, logs, sleeps, paused = _sup([(1, 0, 5)], status="paused")
    assert out == "not-active" and not sleeps and not paused
    assert "not restarting" in logs[-1]


def test_unknown_status_still_restarts():
    # The loop re-checks status itself on start, so restarting when status can't be read is safe.
    out, _, sleeps, _ = _sup([(1, 0, 5), (0, 0, 5)], status=None)
    assert out == "clean" and sleeps == [30]


def test_failed_pause_is_reported():
    _, logs, _, _ = _sup([(1, 0, 5)] * 3, pause_ok=False)
    assert "FAILED (still active?)" in logs[-1]


# --- fix 5: keep the shared boot-log pipe blocking ------------------------------------------------
import fcntl  # noqa: E402
import os  # noqa: E402


def _nonblocking(fd):
    return bool(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK)


def test_ensure_blocking_clears_flag_and_logs():
    r, w = os.pipe()
    fcntl.fcntl(w, fcntl.F_SETFL, fcntl.fcntl(w, fcntl.F_GETFL) | os.O_NONBLOCK)
    logs = []
    assert boot._ensure_blocking_stdio(logs.append, "in test", fds=(w, r)) == [w]
    assert not _nonblocking(w)
    assert len(logs) == 1 and "O_NONBLOCK was set on fd(s)" in logs[0] and "in test" in logs[0]
    os.close(r)
    os.close(w)


def test_ensure_blocking_is_silent_when_already_blocking():
    r, w = os.pipe()
    logs = []
    assert boot._ensure_blocking_stdio(logs.append, "x", fds=(w, 9999)) == []   # bad fd is ignored
    assert logs == []
    os.close(r)
    os.close(w)


def test_installer_output_goes_to_its_own_file(tmp_path, mocker):
    run = mocker.patch.object(boot.subprocess, "run", return_value=mocker.Mock(returncode=0))
    out = tmp_path / "inst.log"
    assert boot._u("curl x | bash", out_path=str(out)) == 0
    kw = run.call_args.kwargs
    assert kw["stdout"].name == str(out) and kw["stderr"] == boot.subprocess.STDOUT
    assert kw["stdin"] == boot.subprocess.DEVNULL
    assert "$ curl x | bash" in out.read_text()
    boot._u("git pull")                               # other commands keep inheriting stdio
    assert "stdout" not in run.call_args.kwargs
