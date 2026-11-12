from aws_auto_research.worker_usage import WorkerUsage


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _usage(states, clock):
    """states: list of (state, launch_epoch) returned by successive describe() calls."""
    it = iter(states)
    last = [("stopped", None)]

    def describe():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return WorkerUsage(describe, clock=clock)


def test_running_period_from_launch_time_to_stop():
    c = Clock(1000)
    u = _usage([("stopped", None), ("running", 1030), ("running", 1030), ("stopped", 1030)], c)
    u.sample()                 # t=1000 stopped
    c.t = 1050; u.sample()     # running since LaunchTime 1030
    c.t = 1200; u.sample()
    c.t = 1290; u.sample()     # stopped observed
    assert u.seconds() == 260  # 1290 - 1030


def test_already_running_is_clamped_to_experiment_start():
    c = Clock(1000)
    u = _usage([("running", 500), ("stopped", 500)], c)   # left running by someone else
    u.sample()
    c.t = 1100; u.sample()
    assert u.seconds() == 100  # attribution starts when this experiment did


def test_minimum_billing_per_start_and_restarts():
    c = Clock(1000)
    u = _usage([("running", 1000), ("stopped", 1000), ("running", 1200), ("stopping", 1200)], c)
    u.sample()                 # running from 1000
    c.t = 1020; u.sample()     # stopped: 20s → billed 60
    c.t = 1300; u.sample()     # restarted at 1200
    c.t = 1500; u.sample()     # stopping: 300s
    assert u.seconds() == 60 + 300


def test_open_period_counts_until_now_and_unknown_is_ignored():
    c = Clock(1000)
    u = _usage([("running", 1000), ("unknown", None)], c)
    u.sample()
    c.t = 1400; u.sample()     # describe failed — not treated as stopped
    assert u.seconds() == 400


def test_wait_until_down():
    c = Clock(0)
    u = _usage([("running", 0), ("stopping", 0)], c)

    def sleep(s):
        c.t += s
    assert u.wait_until_down(timeout_s=60, poll_s=5, sleep=sleep) is True
    c2 = Clock(0)
    stuck = _usage([("running", 0)], c2)
    assert stuck.wait_until_down(timeout_s=20, poll_s=5, sleep=lambda s: setattr(c2, "t", c2.t + s)) is False
