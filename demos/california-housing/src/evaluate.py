"""Fixed, read-only evaluator for the california-housing task. DO NOT EDIT.

Downloads the REAL California Housing dataset (scikit-learn, cached to ~/.sklearn_cache), does a
fixed 70/30 split, imports model.py, fits, and reports held-out R². A failed/invalid candidate
scores -1.0.

Prints a line `r2: <value>` and writes it to metrics.json for Auto-Research (higher is better).
"""

import json
import os
import sys
import numpy as np
from sklearn.datasets import fetch_california_housing
from sklearn.model_selection import train_test_split

DATA_HOME = os.path.expanduser("~/.sklearn_cache")


def main():
    d = fetch_california_housing(data_home=DATA_HOME, as_frame=False)
    X, y = d.data, d.target
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=0)
    try:
        import model
        model.fit(Xtr, ytr)
        yp = np.asarray(model.predict(Xte), dtype=float).reshape(-1)
        if yp.shape[0] != yte.shape[0] or not np.all(np.isfinite(yp)):
            raise ValueError("predict() returned wrong shape or non-finite values")
        ss_res = float(np.sum((yte - yp) ** 2))
        ss_tot = float(np.sum((yte - np.mean(yte)) ** 2))
        r2 = 1.0 - ss_res / ss_tot
    except Exception as e:  # noqa: BLE001
        print(f"model failed: {e}")
        print("r2: -1.0")
        json.dump({"metric": float(-1.0), "status": "invalid"}, open("metrics.json", "w"))
        return
    print(f"n_test: {len(yte)}  n_features: {X.shape[1]}")
    print(f"r2: {r2:.6f}")
    json.dump({"metric": float(r2)}, open("metrics.json", "w"))
    print("(linear baseline ~0.60; FE + gradient boosting reaches ~0.83+)")


if __name__ == "__main__":
    sys.exit(main())
