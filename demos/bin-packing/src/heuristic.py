"""Online bin-packing heuristic candidate — the AI Researcher edits THIS file freely.

`priority(item, bins_remaining)` receives the current item size and a 1-D array of the remaining
capacities of the currently-open bins, and returns an array of scores (one per open bin). The item
is placed in the open bin of HIGHEST priority among those where it fits (remaining >= item); if none
fits, a new bin is opened. The objective (scored by evaluate.py, which you may NOT edit) is to
MINIMIZE the average number of bins used across the real OR-Library instances.

Baseline below: best-fit (prefer the bin with the least leftover after placing). Discover a better
heuristic — this is the classic DeepMind FunSearch task. numpy is available.
"""
import numpy as np


def priority(item, bins_remaining):
    bins_remaining = np.asarray(bins_remaining, dtype=float)
    leftover = bins_remaining - item
    score = -leftover                    # tightest fit first (best-fit)
    score[leftover < 0] = -1e9           # cannot fit here
    return score
