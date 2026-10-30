"""CoverType classification candidate — the AI Researcher edits THIS file freely.

Provide:
  fit(X, y)   — X is (n, 54) float array (cartographic features), y is (n,) class labels 1..7.
  predict(X)  — X is (m, 54); return (m,) predicted class labels.

Objective (scored by evaluate.py, which you may NOT edit): MAXIMIZE held-out accuracy.
Baseline: logistic regression on the raw 54 features (~0.72). The first 10 columns are numeric;
columns 10-53 are binary wilderness-area / soil-type indicators. Engineer features and/or use a
nonlinear model (scikit-learn available — HistGradientBoostingClassifier is strong).
"""
import numpy as np
from sklearn.linear_model import LogisticRegression

_model = None

def fit(X, y):
    global _model
    _model = LogisticRegression(max_iter=200)
    _model.fit(np.asarray(X, dtype=float), np.asarray(y).ravel())

def predict(X):
    return _model.predict(np.asarray(X, dtype=float))
