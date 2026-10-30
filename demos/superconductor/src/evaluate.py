"""Fixed, read-only evaluator for the superconductor task. DO NOT EDIT.

Loads the REAL UCI Superconductivity dataset (see data.py), does a fixed 2/3–1/3 train/test split
GROUPED BY CHEMICAL FORMULA (a material never appears on both sides — the raw data has ~2.3k
formulas listed more than once), imports model.py, fits, and reports held-out RMSE in kelvin.
A failed/invalid candidate scores a large penalty.

Prints a line `rmse: <value>` (lower is better) and writes metrics.json.
"""

import json
import sys

import numpy as np
from sklearn.model_selection import GroupShuffleSplit

import data

SEED = 0
PENALTY = 1000.0


def main():
    X, y = data.load()
    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=1 / 3, random_state=SEED)
                  .split(X, y, groups=X["material"]))
    Xtr, Xte = X.iloc[tr].reset_index(drop=True), X.iloc[te].reset_index(drop=True)
    ytr, yte = y[tr], y[te]
    try:
        import model
        model.fit(Xtr, ytr)
        yp = np.asarray(model.predict(Xte), dtype=float).reshape(-1)
        if yp.shape[0] != yte.shape[0] or not np.all(np.isfinite(yp)):
            raise ValueError("predict() returned wrong shape or non-finite values")
        rmse = float(np.sqrt(np.mean((yte - yp) ** 2)))
        r2 = float(1.0 - np.sum((yte - yp) ** 2) / np.sum((yte - yte.mean()) ** 2))
        mae = float(np.mean(np.abs(yte - yp)))
    except Exception as e:  # noqa: BLE001
        print(f"model failed: {e}")
        print(f"rmse: {PENALTY}")
        json.dump({"metric": PENALTY, "status": "invalid", "notes": str(e)[:500]}, open("metrics.json", "w"))
        return
    print(f"n_train: {len(ytr)}  n_test: {len(yte)}  n_columns: {X.shape[1]}")
    print(f"r2: {r2:.6f}  mae: {mae:.4f}")
    print(f"rmse: {rmse:.6f}")
    json.dump({"metric": rmse, "metrics": {"r2": r2, "mae": mae}}, open("metrics.json", "w"))
    print("(linear baseline ~17.2 K; gradient boosting ~10 K; Hamidieh 2018 XGBoost reference ~9.5 K)")


if __name__ == "__main__":
    sys.exit(main())
