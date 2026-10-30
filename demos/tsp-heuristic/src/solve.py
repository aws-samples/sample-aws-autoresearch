"""TSP heuristic candidate — the AI Researcher edits THIS file freely.

`solve(cities)` receives an (N, 2) float array of city coordinates and must return a tour: a
permutation of range(N) (list or array of ints). The objective (scored by evaluate.py, which you
may NOT edit) is to MINIMIZE the average closed-tour length over the fixed instances.

Baseline below: greedy nearest-neighbour from city 0 (average length ~8.1 on the benchmark).
Large headroom — add 2-opt / Or-opt local search, better construction, or multi-start.
"""

import numpy as np


def solve(cities):
    cities = np.asarray(cities, dtype=float)
    n = len(cities)
    unvisited = list(range(1, n))
    tour = [0]
    cur = 0
    while unvisited:
        d = [(np.hypot(*(cities[cur] - cities[j])), j) for j in unvisited]
        _, nxt = min(d)
        tour.append(nxt)
        unvisited.remove(nxt)
        cur = nxt
    return tour
