"""Fixed, read-only evaluator for the control-policy (Pendulum) task. DO NOT EDIT.

Runs 10 fixed-seed Pendulum-v1 episodes, calling act(obs) each step (torque clipped to [-2, 2]),
and reports the average return. A failed/invalid policy scores -10000.

Prints a line `return: <value>` and writes it to metrics.json for Auto-Research (higher is better).
"""

import json
import sys
import numpy as np

ENV_ID = "Pendulum-v1"
N_EPISODES = 10


def _run():
    import gymnasium as gym
    import policy

    env = gym.make(ENV_ID)
    returns = []
    for seed in range(N_EPISODES):
        obs, _ = env.reset(seed=seed)
        total = 0.0
        done = trunc = False
        while not (done or trunc):
            a = float(np.clip(policy.act(obs), -2.0, 2.0))
            obs, r, done, trunc, _ = env.step(np.array([a], dtype=np.float32))
            total += float(r)
        returns.append(total)
    env.close()
    return float(np.mean(returns))


def main():
    try:
        avg = _run()
        if not np.isfinite(avg):
            raise ValueError("non-finite return")
    except Exception as e:  # noqa: BLE001
        print(f"policy failed: {e}")
        print("return: -10000.0")
        json.dump({"metric": float(-10000.0), "status": "invalid"}, open("metrics.json", "w"))
        return
    print(f"episodes: {N_EPISODES}")
    print(f"return: {avg:.4f}")
    json.dump({"metric": float(avg)}, open("metrics.json", "w"))
    print("(zero-torque baseline ~ -1200; good swing-up + stabilize ~ -150)")


if __name__ == "__main__":
    sys.exit(main())
