"""Fixed, read-only evaluator for the TSP-heuristic task. DO NOT EDIT.

Builds 5 fixed random Euclidean instances (N=100, fixed seed), calls solve(cities) for each,
validates the returned tour is a valid permutation, and reports the AVERAGE closed-tour length.
An invalid/failed tour is penalized heavily (length = 2*N for that instance).

Prints a line `tour_len: <value>` and writes it to metrics.json for Auto-Research (lower is better).
"""

import json
import sys
import numpy as np

N = 100
N_INSTANCES = 5
SEED = 20260818


def _instances():
    rng = np.random.default_rng(SEED)
    return [rng.uniform(0.0, 1.0, (N, 2)) for _ in range(N_INSTANCES)]


def _tour_len(cities, tour):
    idx = np.asarray(tour, dtype=int)
    pts = cities[idx]
    diffs = pts - np.roll(pts, -1, axis=0)   # includes the closing edge (last -> first)
    return float(np.sum(np.hypot(diffs[:, 0], diffs[:, 1])))


def _valid(tour, n):
    try:
        idx = list(np.asarray(tour, dtype=int).reshape(-1))
    except Exception:
        return False
    return len(idx) == n and sorted(idx) == list(range(n))


def main():
    try:
        import solve as solver
    except Exception as e:  # noqa: BLE001
        print(f"import failed: {e}")
        print(f"tour_len: {2 * N}.0")
        json.dump({"metric": float(2 * N), "status": "invalid"}, open("metrics.json", "w"))
        return

    lengths = []
    for k, cities in enumerate(_instances()):
        try:
            tour = solver.solve(cities)
            if not _valid(tour, N):
                raise ValueError("returned tour is not a valid permutation")
            lengths.append(_tour_len(cities, tour))
        except Exception as e:  # noqa: BLE001
            print(f"instance {k} failed: {e}")
            lengths.append(float(2 * N))
    avg = float(np.mean(lengths))
    print(f"instances: {N_INSTANCES}  cities: {N}  per_instance: {[round(x, 3) for x in lengths]}")
    print(f"tour_len: {avg:.6f}")
    json.dump({"metric": float(avg)}, open("metrics.json", "w"))
    print("(nearest-neighbour baseline ~8.1; strong 2-opt ~7.7-7.8)")


if __name__ == "__main__":
    sys.exit(main())
