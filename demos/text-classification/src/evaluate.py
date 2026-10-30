"""Fixed, read-only evaluator for the 20-Newsgroups text-classification task. DO NOT EDIT.

Loads the REAL 20 Newsgroups benchmark (scikit-learn, cached to ~/.sklearn_cache), using the standard
train/test split with headers/footers/quotes removed (the harder, standard setting). Imports
model.py, fits on train, predicts on test, and reports accuracy. A failed/invalid candidate scores
0.0. Prints `accuracy: <value>` (higher is better).
"""
import os, sys, json
import numpy as np
from sklearn.datasets import fetch_20newsgroups
from sklearn.metrics import accuracy_score

DATA_HOME = os.path.expanduser("~/.sklearn_cache")
REMOVE = ("headers", "footers", "quotes")


def main():
    tr = fetch_20newsgroups(subset="train", remove=REMOVE, data_home=DATA_HOME)
    te = fetch_20newsgroups(subset="test", remove=REMOVE, data_home=DATA_HOME)
    try:
        import model
        model.fit(tr.data, tr.target)
        yp = np.asarray(model.predict(te.data)).reshape(-1)
        if yp.shape[0] != len(te.target):
            raise ValueError("predict() returned wrong length")
        acc = float(accuracy_score(te.target, yp))
    except Exception as e:  # noqa: BLE001
        print(f"model failed: {e}")
        print("accuracy: 0.0")
        json.dump({"metric": float(0.0), "status": "invalid"}, open("metrics.json", "w"))
        return
    print(f"n_train: {len(tr.data)}  n_test: {len(te.data)}  classes: 20")
    print(f"accuracy: {acc:.6f}")
    json.dump({"metric": float(acc)}, open("metrics.json", "w"))
    print("(TF-IDF + logistic baseline ~0.69; tuned features + model reaches ~0.80+)")


if __name__ == "__main__":
    sys.exit(main())
