"""Circle packing candidate — the AI Researcher edits THIS file freely.

`construct(n)` must return a list of `n` circles as (x, y, r) tuples, all inside
the unit square [0,1]x[0,1] and pairwise non-overlapping. The objective (scored by
evaluate.py, which you may NOT edit) is to MAXIMIZE the sum of the radii.

Baseline below: a trivial equal-circle square grid (sum of radii ~2.09 for n=26).
There is large headroom — see the ideas in auto-research.json.
"""

import math


def construct(n: int) -> list[tuple[float, float, float]]:
    # Equal circles on the smallest square grid that fits n cells.
    cols = math.ceil(math.sqrt(n))
    # Cell size 1/cols; radius just under half a cell so neighbors don't overlap.
    r = 0.49 / cols
    circles: list[tuple[float, float, float]] = []
    for i in range(n):
        row, col = divmod(i, cols)
        x = (col + 0.5) / cols
        y = (row + 0.5) / cols
        circles.append((x, y, r))
    return circles
