"""California-housing regression candidate — the AI Researcher edits THIS file freely.

Provide:
  fit(X, y)   — X is (n, 8) float array of the raw California Housing features, y is (n,) float.
  predict(X)  — X is (m, 8); return (m,) predicted median house value.

Raw feature order: [MedInc, HouseAge, AveRooms, AveBedrms, Population, AveOccup, Latitude, Longitude].
The objective (scored by evaluate.py, which you may NOT edit) is to MAXIMIZE held-out R².
The baseline is an ordinary linear regression on the raw 8 features (R² ~0.60). Engineer features
(geographic, ratios, transforms) and/or use a nonlinear model (scikit-learn available).
"""

import numpy as np
from sklearn.linear_model import LinearRegression

_model = None


def fit(X, y):
    global _model
    _model = LinearRegression()
    _model.fit(np.asarray(X, dtype=float), np.asarray(y, dtype=float).ravel())


def predict(X):
    return _model.predict(np.asarray(X, dtype=float))
