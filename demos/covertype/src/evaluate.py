"""Fixed, read-only evaluator for the covertype task. DO NOT EDIT.

Downloads the REAL Forest CoverType benchmark (scikit-learn, cached to ~/.sklearn_cache), takes a
fixed 100k subsample (seed 0), does a fixed 70/30 stratified split, imports model.py, fits, and
reports held-out accuracy. A failed/invalid candidate scores 0.0. Prints `accuracy: <value>`.
"""
import os, sys, json
import numpy as np
from sklearn.datasets import fetch_covtype
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

DATA_HOME = os.path.expanduser("~/.sklearn_cache")
SUB, SEED = 100000, 0

def main():
    d = fetch_covtype(data_home=DATA_HOME)
    X, y = d.data, d.target
    rng = np.random.default_rng(SEED)
    idx = rng.choice(len(y), size=min(SUB, len(y)), replace=False)
    X, y = X[idx], y[idx]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=SEED, stratify=y)
    try:
        import model
        model.fit(Xtr, ytr)
        yp = np.asarray(model.predict(Xte)).reshape(-1)
        if yp.shape[0] != yte.shape[0]:
            raise ValueError("predict() returned wrong shape")
        acc = float(accuracy_score(yte, yp))
    except Exception as e:  # noqa: BLE001
        print(f"model failed: {e}")
        print("accuracy: 0.0")
        json.dump({"metric": float(0.0), "status": "invalid"}, open("metrics.json", "w"))
        return
    print(f"n_test: {len(yte)}  n_features: {X.shape[1]}  classes: {len(set(yte.tolist()))}")
    print(f"accuracy: {acc:.6f}")
    json.dump({"metric": float(acc)}, open("metrics.json", "w"))
    print("(logistic baseline ~0.72; FE + gradient boosting reaches ~0.87+)")

if __name__ == "__main__":
    sys.exit(main())
