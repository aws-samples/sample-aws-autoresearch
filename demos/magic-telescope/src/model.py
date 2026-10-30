"""MagicTelescope classification candidate — the AI Researcher edits THIS file freely.

Provide:
  fit(X, y)          — X is (n, 10) float array of telescope-image features, y is (n,) 0/1 labels.
  predict_proba(X)   — X is (m, 10); return a 1-D array of P(class=1), length m.

The objective (scored by evaluate.py, which you may NOT edit) is to MAXIMIZE held-out ROC AUC.
The baseline is a plain logistic regression on the raw 10 features (AUC ~0.84). Engineer features
(ratios, products, logs of the shower-shape variables) and/or use a nonlinear model (sklearn avail.).
"""
import numpy as np
from sklearn.linear_model import LogisticRegression

_model = None

def fit(X, y):
    global _model
    _model = LogisticRegression(max_iter=1000)
    _model.fit(np.asarray(X, dtype=float), np.asarray(y).ravel())

def predict_proba(X):
    return _model.predict_proba(np.asarray(X, dtype=float))[:, 1]
