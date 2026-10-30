# tsp-heuristic

Design a better Traveling-Salesman heuristic. The evaluator builds a fixed set of random Euclidean TSP instances (100 cities each) and you edit `src/solve.py` — `solve(cities) -> tour` — to **minimize** the average closed-tour length (`tour_len`, lower is better).

A nearest-neighbour baseline (~9.4) leaves large headroom; 2-opt / Or-opt / better construction push it below ~7.9. This is a beyond-ML demo: pure CPU, the evaluator runs in ~1 s, so you can iterate on the heuristic quickly and watch tour length drop.

## Background

Euclidean Traveling Salesman Problem: given N=100 city coordinates in the unit square, output a tour (a permutation of `0..N-1`) minimizing the total closed-loop distance. The read-only evaluator `src/evaluate.py` builds 5 fixed random instances (fixed seed), calls `solve(cities)` for each, validates the returned tour is a valid permutation, and reports the **average** closed-tour length; an invalid or failed tour is penalized heavily.

The baseline `src/solve.py` is a greedy nearest-neighbour construction (average length ~9.4). Run the baseline first to establish the reference. Classic, CPU-cheap levers close most of the gap: 2-opt and Or-opt local search (repeatedly uncross edges), better construction (greedy-edge / Christofides-like), or multi-start with different seeds keeping the best. numpy is available. The evaluator is deterministic and fast (~1 s), so iterate freely; strong 2-opt typically reaches ~7.9, and multi-start/Or-opt below.

## What the agent optimizes

- **Edits `src/solve.py`** — the `solve(cities) -> tour` function that produces a permutation of `0..N-1`.
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — the fixed harness that builds instances, validates tours, and computes the metric.
- **Metric:** `tour_len` (lower is better), from `run.log`.

## Ideas to try

- Run the nearest-neighbour baseline to establish the starting average tour length (~9.4)
- Add 2-opt local search over the baseline tour until no improving swap remains
- Add Or-opt moves (relocate segments of length 1-3) alongside 2-opt
- Try greedy-edge construction instead of nearest-neighbour, then 2-opt
- Multi-start: run several nearest-neighbour starts + 2-opt and keep the best tour

## References

- [2-opt (Wikipedia)](https://en.wikipedia.org/wiki/2-opt)
- [Travelling salesman problem (Wikipedia)](https://en.wikipedia.org/wiki/Travelling_salesman_problem)
