# circle-packing

Pack **26 non-overlapping circles** inside the unit square `[0,1] x [0,1]` to **maximize the sum of the radii** (`sum_radii`, higher is better). The construction/optimization strategy is entirely up to the agent. This is the benchmark configuration DeepMind's AlphaEvolve famously improved (n=26), reporting a configuration with sum of radii around **2.635**.

The evaluator runs in well under a second on pure CPU, so many strategies can be tried quickly while watching the metric climb.

## Background

Circle packing for maximum sum of radii: place N=26 circles with centers `(x, y)` and radii `r` inside the unit square such that no two circles overlap, maximizing the sum of the radii. This is a classic packing problem; DeepMind's AlphaEvolve reported a configuration with sum of radii around 2.635 for n=26, improving on the prior state of the art.

The evaluator imports `construct(n)` from `src/pack.py`, which must return a list of `(x, y, r)` tuples. It checks validity (every circle inside the square, all pairs non-overlapping within a 1e-6 tolerance) and reports the sum of the radii, scoring 0 for any invalid configuration.

The baseline in `src/pack.py` is a trivial equal-circle grid packing (sum ~2.09) — there is large headroom. **Run the baseline first** to establish the reference `sum_radii`, then try to beat it. Key levers: use unequal radii (bigger circles in the middle, small ones filling gaps and corners), hexagonal rather than square arrangements, and iterative refinement (grow each circle to touch its nearest neighbor / the walls, then nudge centers apart — a physics-style relaxation, or simulated annealing / gradient ascent on center positions). The evaluator is deterministic and runs in milliseconds, so try many ideas.

## What the agent optimizes

- **Edits `src/pack.py`** — the `construct(n) -> list[(x, y, r)]` function that produces the circle configuration (centers and radii).
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — the fixed harness: validity checks (inside-square, pairwise non-overlap within 1e-6) and the `sum_radii` metric.
- **Metric:** `sum_radii` (higher is better), from `run.log`.

## Ideas to try

- Run the baseline grid packing to establish the starting `sum_radii` (~2.09).
- Give circles unequal radii: after placing centers, grow each circle to the largest radius that keeps it inside the square and non-overlapping (greedy inflation).
- Use a hexagonal center arrangement instead of a square grid, then greedily inflate.
- Add an iterative relaxation: alternately inflate all circles and push overlapping/crowded centers apart, repeating until stable.
- Optimize center positions with simulated annealing or gradient ascent on the sum of (inflated) radii.
- Special-case the corners and edges — circles there can be larger since fewer neighbors constrain them.

## References

- [AlphaEvolve: a Gemini-powered coding agent for designing advanced algorithms (DeepMind)](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
- [Circle packing in a square (Wikipedia)](https://en.wikipedia.org/wiki/Circle_packing_in_a_square)
