"""Control-policy candidate — the AI Researcher edits THIS file freely.

`act(obs)` receives the Pendulum observation [cos(theta), sin(theta), theta_dot] and must return a
scalar torque (it will be clipped to [-2, 2]). The objective (scored by evaluate.py, which you may
NOT edit) is to MAXIMIZE the average episode return (return is negative; higher/closer-to-0 = better).

Baseline below: zero torque — the pendulum just hangs (return ~ -1200). Large headroom: add an
energy-based swing-up and a PD/LQR stabilizer near the top. numpy is available.
"""


def act(obs):
    # obs = [cos(theta), sin(theta), theta_dot]; theta measured from upright.
    return 0.0
