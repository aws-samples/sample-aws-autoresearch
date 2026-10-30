# bin-packing

Discover a better **online bin-packing heuristic** — the classic DeepMind FunSearch discovery task — on the **real OR-Library Falkenauer U120** benchmark (20 instances, bin capacity 150, 120 items each, with published optima). The agent edits the `priority(item, bins_remaining)` rule to **minimize the average number of bins used** (`mean_bins`, lower is better). Best-fit averages ~51.9 bins; the known optimum is ~49.15, so there is real room for a smarter priority rule.

## Background

Online bin packing is DeepMind FunSearch's flagship discovery task. Items arrive one at a time and each must be placed immediately and irrevocably: the goal is to use as few unit-capacity bins as possible over the whole stream.

Evaluation uses the **real OR-Library Falkenauer U120** instances (`binpack1.txt`: 20 problems, capacity 150, 120 items each, best-known optima averaging ~49.15). `setup.sh` downloads and caches the data to `~/.bpp/binpack1.txt`. The read-only evaluator loads the real instances and packs each one **online in file order**: for every item it calls `priority(item, bins_remaining)` — which returns a 1-D array of scores over the currently-open bins — and places the item in the *fitting* open bin of highest priority, else opens a new bin. It reports the average number of bins used across the 20 instances (an invalid heuristic scores 999).

The baseline `src/heuristic.py` is **best-fit** (prefer the tightest-fitting bin), which averages ~51.9 bins. **Run the baseline first** to establish the starting `mean_bins` (~51.9), then try to beat it toward the ~49.15 optimum. numpy is available and the evaluator runs in milliseconds, so many heuristic variants can be tried across many experiments.

## What the agent optimizes

- **Edits `src/heuristic.py`** — the `priority(item, bins_remaining)` function returning a score per open bin; the item goes into the highest-priority fitting bin.
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — fixed harness that loads the real instances, packs online, and computes the metric.
- **Metric:** `mean_bins` (lower is better), from `run.log`.

## Ideas to try

- Run the best-fit baseline to establish the starting average number of bins (~51.9)
- Try the FunSearch-discovered style: penalize leftovers that are small-but-nonzero (awkward gaps) more than exact fits
- Score bins by a nonlinear function of remaining capacity (e.g., reward remaining close to 0 or close to capacity)
- Add a threshold rule: strongly prefer bins that the item fills to >90% capacity
- Combine multiple terms (tightness + fill-fraction) and tune their weights

## References

- [FunSearch: Mathematical discoveries from program search (Nature, 2023)](https://www.nature.com/articles/s41586-023-06924-6)
- [OR-Library bin-packing benchmark information](https://people.brunel.ac.uk/~mastjjb/jeb/orlib/binpackinfo.html)
