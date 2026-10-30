"""TSPLIB heuristic candidate — the AI Researcher edits THIS file freely.

`solve(coords)` receives an (N, 2) float array of city coordinates for one TSPLIB instance and must
return a tour: a permutation of range(N). Objective (scored by evaluate.py, which you may NOT edit):
MINIMIZE the average optimality gap (%) across the real TSPLIB instances vs their published optima.

Baseline below: nearest-neighbour from city 0 (~20-25% gap). Add 2-opt / Or-opt local search,
better construction (greedy-edge), or multi-start to close the gap. numpy is available.
"""
import numpy as np


def solve(coords):
    coords = np.asarray(coords, dtype=float)
    n = len(coords)
    unvisited = set(range(1, n))
    tour = [0]
    cur = 0
    while unvisited:
        nxt = min(unvisited, key=lambda j: (coords[cur, 0] - coords[j, 0]) ** 2 + (coords[cur, 1] - coords[j, 1]) ** 2)
        tour.append(nxt); unvisited.discard(nxt); cur = nxt
    return tour
