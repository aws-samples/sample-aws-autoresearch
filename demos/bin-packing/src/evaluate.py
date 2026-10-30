"""Fixed, read-only evaluator for the online bin-packing task. DO NOT EDIT.

Loads the REAL OR-Library Falkenauer U120 instances (binpack1.txt, downloaded by setup.sh to
~/.bpp/binpack1.txt), and for each instance packs items ONLINE (in file order) using the candidate's
priority(item, bins_remaining). Reports the average number of bins used across the 20 instances.
A failed/invalid candidate scores a large penalty. Prints `mean_bins: <value>` (LOWER is better).
"""
import os, sys, json
import numpy as np

DATA = os.path.expanduser("~/.bpp/binpack1.txt")
PENALTY = 999.0


def _load():
    toks = open(DATA).read().split()
    i = 0
    n_prob = int(toks[i]); i += 1
    problems = []
    for _ in range(n_prob):
        name = toks[i]; i += 1
        cap = int(toks[i]); n = int(toks[i + 1]); best = int(toks[i + 2]); i += 3
        items = [int(toks[i + k]) for k in range(n)]; i += n
        problems.append((cap, items, best))
    return problems


def _pack(cap, items):
    import heuristic as H  # noqa
    open_bins = []  # remaining capacities
    for it in items:
        if open_bins:
            scores = np.asarray(H.priority(it, list(open_bins)), dtype=float).reshape(-1)
            if scores.shape[0] != len(open_bins) or not np.all(np.isfinite(scores[scores > -1e18])):
                raise ValueError("priority returned wrong shape / bad values")
            feasible = [j for j in range(len(open_bins)) if open_bins[j] >= it]
            if feasible:
                j = max(feasible, key=lambda k: scores[k])
                open_bins[j] -= it
                continue
        open_bins.append(cap - it)  # open a new bin
    return len(open_bins)


def main():
    try:
        problems = _load()
    except Exception as e:  # noqa: BLE001
        print(f"could not load instances: {e}")
        print(f"mean_bins: {PENALTY}")
        json.dump({"metric": float(PENALTY), "status": "invalid",
                   "notes": f"could not load instances: {e}"}, open("metrics.json", "w"))
        return
    try:
        used, opt = [], []
        for cap, items, best in problems:
            used.append(_pack(cap, items)); opt.append(best)
        mean_bins = float(np.mean(used))
    except Exception as e:  # noqa: BLE001
        print(f"heuristic failed: {e}")
        print(f"mean_bins: {PENALTY}")
        json.dump({"metric": float(PENALTY), "status": "invalid",
                   "notes": f"heuristic failed: {e}"}, open("metrics.json", "w"))
        return
    print(f"instances: {len(problems)}  mean_optimal: {np.mean(opt):.2f}")
    print(f"mean_bins: {mean_bins:.4f}")
    json.dump({"metric": float(mean_bins)}, open("metrics.json", "w"))
    print("(best-fit baseline is close but not optimal; FunSearch-style heuristics reduce bins)")


if __name__ == "__main__":
    sys.exit(main())
