"""Fixed, read-only evaluator for the circle-packing task. DO NOT EDIT.

Imports construct(n) from pack.py, validates the returned circles, and reports the
sum of radii. Any invalid configuration scores 0. Deterministic; runs in milliseconds.

Prints a line `sum_radii: <value>` and writes it to metrics.json for Auto-Research.
"""

import json
import math
import sys

N = 26            # AlphaEvolve's benchmark configuration
TOL = 1e-6        # tolerance for "inside" and "non-overlapping" checks


def _validate(circles, n):
    if not isinstance(circles, (list, tuple)):
        return False, "construct() must return a list of (x, y, r) tuples"
    if len(circles) != n:
        return False, f"expected {n} circles, got {len(circles)}"
    pts = []
    for k, c in enumerate(circles):
        try:
            x, y, r = float(c[0]), float(c[1]), float(c[2])
        except Exception:
            return False, f"circle {k} is not a numeric (x, y, r) triple"
        if not all(math.isfinite(v) for v in (x, y, r)):
            return False, f"circle {k} has non-finite values"
        if r <= 0:
            return False, f"circle {k} has non-positive radius"
        if x - r < -TOL or x + r > 1 + TOL or y - r < -TOL or y + r > 1 + TOL:
            return False, f"circle {k} escapes the unit square"
        pts.append((x, y, r))
    for i in range(n):
        xi, yi, ri = pts[i]
        for j in range(i + 1, n):
            xj, yj, rj = pts[j]
            d = math.hypot(xi - xj, yi - yj)
            if d < ri + rj - TOL:
                return False, f"circles {i} and {j} overlap (d={d:.6f} < {ri + rj:.6f})"
    return True, ""


def main():
    try:
        import pack
        circles = pack.construct(N)
    except Exception as e:  # noqa: BLE001
        print(f"construct() raised: {e}")
        print("sum_radii: 0.0")
        json.dump({"metric": float(0.0), "status": "invalid"}, open("metrics.json", "w"))
        return

    ok, msg = _validate(circles, N)
    if not ok:
        print(f"INVALID: {msg}")
        print("sum_radii: 0.0")
        json.dump({"metric": float(0.0), "status": "invalid"}, open("metrics.json", "w"))
        return

    total = sum(float(c[2]) for c in circles)
    print(f"n: {N}  circles: {len(circles)}  valid: True")
    print(f"sum_radii: {total:.6f}")
    print(f"(AlphaEvolve reference for n=26 ≈ 2.635)")
    json.dump({"metric": float(total)}, open("metrics.json", "w"))


if __name__ == "__main__":
    sys.exit(main())
