# magic-telescope

A Kaggle-style tabular **binary-classification** problem on the **real MAGIC Gamma Telescope** benchmark (OpenML data_id 1120; a UCI dataset widely used in ML papers). Cherenkov-shower images must be classified as gamma vs hadron. You edit `src/model.py` — `fit(X, y)` then `predict_proba(X)` — to **maximize held-out ROC AUC** (higher is better).

A plain logistic-regression baseline (~0.842) leaves clear headroom; feature engineering on the shower-shape variables plus a gradient-boosting model pushes AUC toward ~0.93. Real data, pure CPU, ~seconds per evaluation, so you can iterate across many experiments.

## Background

The dataset is the real MAGIC Gamma Telescope benchmark: 19,020 rows, 10 numeric shower-shape features — `fLength`, `fWidth`, `fSize`, `fConc`, `fConc1`, `fAsym`, `fM3Long`, `fM3Trans`, `fAlpha`, `fDist` — with target gamma (`'g'`) vs hadron (`'h'`). The read-only evaluator downloads the real dataset, does a fixed 70/30 stratified split, calls `model.fit(X_train, y_train)` then `model.predict_proba(X_test)` (1-D P(class=1)), and reports held-out ROC AUC (a failed candidate scores 0.0).

The baseline `src/model.py` is a logistic regression on the raw 10 features (AUC ~0.842). Run the baseline first to establish the reference AUC, then try to beat it. Key levers: engineer features (ratios/products of the shape variables, logs of the skewed size/length features, interaction terms, polynomial features), standardize, and switch to a nonlinear model — scikit-learn's `HistGradientBoostingClassifier` is available and reaches ~0.93.

## What the agent optimizes

- **Edits `src/model.py`** — implements `fit(X, y)` and `predict_proba(X)`; controls feature engineering and model choice.
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — the fixed harness: dataset download, 70/30 stratified split, and ROC AUC scoring.
- **Metric:** `roc_auc` (higher is better), from `run.log`.

## Ideas to try

- Run the logistic-regression baseline on the raw 10 features to establish the starting ROC AUC (~0.84).
- Swap the model to `HistGradientBoostingClassifier` on the raw features.
- Add log transforms of the skewed size/length features and standardize.
- Add ratio and product interaction features between shower-shape variables, then refit.
- Add polynomial features and tune the boosting hyperparameters (depth, learning rate, iterations).

## References

- [MAGIC Gamma Telescope on OpenML (data_id 1120)](https://www.openml.org/d/1120)
- [MAGIC Gamma Telescope — UCI Machine Learning Repository](https://archive.ics.uci.edu/dataset/159/magic+gamma+telescope)
