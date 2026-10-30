"""Superconductor Tc regression candidate — the AI Researcher edits THIS file freely.

Provide:
  fit(X, y)   — X is a pandas DataFrame (one row per material), y is (n,) critical temperature in K.
  predict(X)  — X is a DataFrame with the same columns; return (m,) predicted Tc in K.

Columns of X, in order:
  - `material`: the chemical formula string, e.g. "Ba0.2La1.8Cu1O4"
  - 81 composition descriptors from Hamidieh (2018): `number_of_elements`, then mean / weighted
    mean / geometric mean / entropy / range / std (plain and weighted) of 8 elemental properties
    (atomic mass, first ionization energy, atomic radius, density, electron affinity, fusion heat,
    thermal conductivity, valence)
  - 86 element amounts `H` … `Rn` (stoichiometric coefficients parsed from the formula)

The objective (scored by evaluate.py, which you may NOT edit) is to MINIMIZE held-out RMSE.
The baseline is an ordinary linear regression on the 81 descriptors (RMSE ~17.2 K).
"""

import numpy as np
from sklearn.linear_model import LinearRegression

_model = None


def _features(X):
    return X.iloc[:, 1:82].to_numpy(dtype=float)  # the 81 Hamidieh descriptors


def fit(X, y):
    global _model
    _model = LinearRegression()
    _model.fit(_features(X), np.asarray(y, dtype=float).ravel())


def predict(X):
    return _model.predict(_features(X))
