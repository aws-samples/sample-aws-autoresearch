# control-policy

Discover a control policy for the classic **Pendulum swing-up** task. You edit `src/policy.py`
— `act(obs) -> torque` — to **maximize `return`** (higher is better), the average episode return
over a fixed set of seeds. An under-actuated pendulum starts near the bottom and must be swung up
and balanced upright; a zero-torque baseline scores around -1200, while an energy-based swing-up
plus PD stabilization reaches roughly -150 — large headroom. This is a beyond-ML, pure-CPU setting
(Gymnasium classic control): each evaluation runs in about 1 second, so you can iterate on the
controller quickly and watch return climb.

## Background

The task is `Pendulum-v1` from Gymnasium classic control. The observation is
`[cos(theta), sin(theta), theta_dot]`; the action is a torque in `[-2, 2]`; the per-step reward is
`-(theta^2 + 0.1*theta_dot^2 + 0.001*torque^2)` over 200 steps. Return is therefore always negative
and higher (closer to 0) is better. The evaluator runs 10 fixed-seed episodes, calls `act(obs)`
each step (clipping torque to `[-2, 2]`), and reports the average return; a failed or invalid policy
scores -10000. The baseline `src/policy.py` outputs zero torque, so the pendulum just hangs and
returns about -1200. Run the baseline first to establish the reference return, then try to beat it.
A good swing-up + stabilize controller reaches roughly -150. `numpy` is available and the evaluator
is deterministic and fast, so iterate freely.

## What the agent optimizes

- **Edits `src/policy.py`** — implements `act(obs) -> torque` (clipped to `[-2, 2]`), the controller
  that decides the torque to apply each step.
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — the fixed harness runs the 10
  fixed-seed `Pendulum-v1` episodes and computes the metric.
- **Metric:** `return` (higher is better), from `run.log`.

## Ideas to try

- Run the zero-torque baseline to establish the starting average return (~ -1200).
- Add a proportional-derivative stabilizer near the top (`torque = -k1*theta - k2*theta_dot` for
  small angle).
- Add energy-based swing-up: when far from upright, apply torque in the sign of
  `(theta_dot * cos(theta))` to pump energy.
- Combine swing-up (far from top) with PD stabilization (near top) and tune the switch threshold.
- Grid/coordinate-search the gains to squeeze out the best average return.
- Consider an LQR-style linear stabilizer near upright as an alternative to hand-tuned PD gains.

## References

- [Pendulum-v1 / Gymnasium classic control (arXiv:2407.17032)](https://arxiv.org/abs/2407.17032)
- [Inverted pendulum (Wikipedia)](https://en.wikipedia.org/wiki/Inverted_pendulum)
