"""Fixed, read-only evaluator for the TSPLIB task. DO NOT EDIT.

Loads the REAL TSPLIB instances (downloaded by setup.sh to ~/.tsplib/), and for each calls
solve(coords), validates the tour is a permutation, computes the TSPLIB EUC_2D tour length
(nint-rounded, the standard convention), and reports the average optimality GAP (%) vs the published
optima. A failed/invalid candidate scores a large penalty. Prints `gap_pct: <value>` (LOWER better).
"""
import os, sys, math, json
import numpy as np

TSPDIR = os.path.expanduser("~/.tsplib")
# Published optimal tour lengths (TSPLIB EUC_2D).
OPTIMA = {"berlin52": 7542, "eil51": 426, "eil76": 538, "kroA100": 21282, "ch150": 6528}
PENALTY = 500.0


def _parse(path):
    coords = {}
    reading = False
    for line in open(path):
        s = line.strip()
        if s.startswith("NODE_COORD_SECTION"):
            reading = True; continue
        if not reading:
            continue
        if s in ("EOF", "") or s.startswith("EOF"):
            break
        parts = s.split()
        if len(parts) >= 3:
            try:
                idx = int(parts[0]); coords[idx] = (float(parts[1]), float(parts[2]))
            except ValueError:
                break
    ids = sorted(coords)
    return np.array([coords[i] for i in ids], dtype=float)


def _euc2d_len(coords, tour):
    t = np.asarray(tour, dtype=int)
    pts = coords[t]
    nxt = np.roll(pts, -1, axis=0)
    d = np.rint(np.sqrt(((pts - nxt) ** 2).sum(1)))   # nint per TSPLIB EUC_2D
    return float(d.sum())


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
        print(f"import failed: {e}"); print(f"gap_pct: {PENALTY}")
        json.dump({"metric": float(PENALTY), "status": "invalid"}, open("metrics.json", "w")); return
    gaps = []
    for name, opt in OPTIMA.items():
        path = os.path.join(TSPDIR, name + ".tsp")
        if not os.path.exists(path):
            continue
        coords = _parse(path); n = len(coords)
        try:
            tour = solver.solve(coords)
            if not _valid(tour, n):
                raise ValueError("tour is not a valid permutation")
            L = _euc2d_len(coords, tour)
            gaps.append(100.0 * (L / opt - 1.0))
        except Exception as e:  # noqa: BLE001
            print(f"{name} failed: {e}"); gaps.append(PENALTY)
    if not gaps:
        print("no instances found"); print(f"gap_pct: {PENALTY}")
        json.dump({"metric": float(PENALTY), "status": "invalid"}, open("metrics.json", "w")); return
    mean_gap = float(np.mean(gaps))
    print(f"instances: {len(gaps)}  per_instance_gap%: {[round(g,2) for g in gaps]}")
    print(f"gap_pct: {mean_gap:.4f}")
    json.dump({"metric": float(mean_gap)}, open("metrics.json", "w"))
    print("(nearest-neighbour baseline ~20-25% gap; strong 2-opt/Or-opt reaches a few %)")


if __name__ == "__main__":
    sys.exit(main())
