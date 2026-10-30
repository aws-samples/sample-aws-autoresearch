# tsp-tsplib

Design a Traveling-Salesman heuristic on the REAL TSPLIB benchmark (berlin52, eil51, eil76, kroA100, ch150 — classic instances with published optimal tour lengths). You edit `src/solve.py` — `solve(coords) -> tour` — to MINIMIZE the average optimality gap (%) versus the known optima; the reported metric is `gap_pct` (lower is better). Nearest-neighbour leaves a ~24% gap; 2-opt / Or-opt / Lin-Kernighan-style local search and multi-start close it to a few percent. Real benchmark data, pure CPU, ~seconds per evaluation, so you can try many heuristics across many experiments.

## Background

This is the Euclidean Traveling Salesman Problem on the REAL TSPLIB benchmark. The read-only evaluator `src/evaluate.py` loads five classic instances (berlin52 opt 7542, eil51 opt 426, eil76 opt 538, kroA100 opt 21282, ch150 opt 6528; cached to `~/.tsplib`), calls `solve(coords)` for each, validates the returned tour is a valid permutation, computes the TSPLIB EUC_2D tour length (nint-rounded — the standard convention), and reports the AVERAGE optimality gap % versus the published optima (invalid/failed tours are penalized). The baseline `src/solve.py` is a greedy nearest-neighbour construction (~24% gap), leaving large headroom.

Classic CPU-cheap heuristics apply: 2-opt and Or-opt local search (repeatedly uncross edges / relocate short segments), greedy-edge or Christofides-like construction, and multi-start keeping the best tour. numpy is available. Run the baseline first to establish the reference gap, then iterate toward a few-percent gap.

## What the agent optimizes

- **Edits `src/solve.py`** — the `solve(coords) -> tour` function that returns a permutation of the city indices; this is the heuristic under optimization.
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — the fixed harness: instance loading, TSPLIB EUC_2D (nint-rounded) length computation, tour validation, and gap-vs-optima scoring.
- **Metric:** `gap_pct` (lower is better), from `run.log`.

## Ideas to try

- Run the nearest-neighbour baseline to establish the starting mean gap (~24%)
- Add 2-opt local search until no improving swap remains
- Add Or-opt moves (relocate segments of length 1-3) alongside 2-opt
- Try multiple nearest-neighbour starts + 2-opt and keep the best tour per instance
- Add a Lin-Kernighan-style sequential edge exchange
- Try greedy-edge or Christofides-like construction as the starting tour before local search

## References

- [Lin–Kernighan heuristic (Wikipedia)](https://en.wikipedia.org/wiki/Lin%E2%80%93Kernighan_heuristic)
- [2-opt (Wikipedia)](https://en.wikipedia.org/wiki/2-opt)

## Notes

- Tour lengths use the TSPLIB EUC_2D convention (round each edge distance to the nearest integer, `nint`); optimize against that rounded length, not raw floating-point distance, since the gap is measured against published optima under this convention.
- The evaluator is deterministic and runs in ~seconds, so iterate freely.
