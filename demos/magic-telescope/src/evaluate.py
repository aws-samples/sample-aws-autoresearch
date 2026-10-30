"""Fixed, read-only evaluator for the magic-telescope task. DO NOT EDIT.

Downloads the REAL MAGIC Gamma Telescope benchmark (OpenML data_id 1120, cached to ~/.sklearn_cache),
does a fixed 70/30 stratified split, imports model.py, fits, and reports held-out ROC AUC.
A failed/invalid candidate scores 0.0. Prints `roc_auc: <value>` (higher is better).
"""
import os, sys, json
import numpy as np
from sklearn.datasets import fetch_openml
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score

DATA_HOME = os.path.expanduser("~/.sklearn_cache")
SEED = 0

def main():
    d = fetch_openml(data_id=1120, as_frame=True, data_home=DATA_HOME)
    X = d.data.apply(lambda c: c.astype(float)).values
    classes = sorted(d.target.astype(str).unique())
    y = (d.target.astype(str) == classes[0]).astype(int).values
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=SEED, stratify=y)
    try:
        import model
        model.fit(Xtr, ytr)
        p = np.asarray(model.predict_proba(Xte), dtype=float).reshape(-1)
        if p.shape[0] != yte.shape[0] or not np.all(np.isfinite(p)):
            raise ValueError("predict_proba() returned wrong shape or non-finite values")
        auc = float(roc_auc_score(yte, p))
    except Exception as e:  # noqa: BLE001
        print(f"model failed: {e}")
        print("roc_auc: 0.0")
        json.dump({"metric": float(0.0), "status": "invalid"}, open("metrics.json", "w"))
        return
    print(f"n_test: {len(yte)}  n_features: {X.shape[1]}")
    print(f"roc_auc: {auc:.6f}")
    json.dump({"metric": float(auc)}, open("metrics.json", "w"))
    print("(logistic baseline ~0.842; FE + gradient boosting reaches ~0.93+)")

if __name__ == "__main__":
    sys.exit(main())
