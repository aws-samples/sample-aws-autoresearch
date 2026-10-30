# covertype

A large-scale tabular multiclass-classification problem on the real Forest CoverType benchmark
(scikit-learn / UCI; 581,012 rows, 54 features, 7 forest-cover classes). You edit `src/model.py`
— `fit(X, y)` then `predict(X)` — to maximize held-out **accuracy** (higher is better) on a fixed
100k subsample.

A plain logistic-regression baseline (~0.64–0.72) leaves large headroom: feature engineering on
the numeric cartographic columns plus a gradient-boosting model pushes accuracy toward ~0.87+.

## Background

Real Forest CoverType multiclass classification (scikit-learn `fetch_covtype`). The read-only
evaluator takes a fixed 100k subsample (seed 0), does a fixed 70/30 stratified split, calls
`model.fit(X_train, y_train)` then `model.predict(X_test)`, and reports test accuracy (a failed
candidate scores 0.0). The baseline `src/model.py` is logistic regression on the raw 54 features
(~0.64–0.72): the first 10 columns are numeric cartographic features, and columns 10–53 are binary
wilderness-area / soil-type indicators.

Run the logistic baseline first to establish the reference accuracy, then try to beat it. Key levers:
engineer the numeric columns (hillshade interactions, distance combinations, elevation bands,
standardization) while keeping the binary indicators, and switch to a nonlinear model — scikit-learn's
`HistGradientBoostingClassifier` reaches ~0.87+, and tuning (`max_leaf_nodes`, `learning_rate`,
iterations) pushes further.

## What the agent optimizes

- **Edits `src/model.py`** — `fit(X, y)` and `predict(X)` for the 7 forest-cover classes; feature engineering and model choice are yours.
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — the fixed harness: 100k subsample, 70/30 stratified split, and accuracy metric.
- **Metric:** `accuracy` (higher is better), from `run.log`.

## Ideas to try

- Run the logistic baseline to establish the starting accuracy
- Swap the model to `HistGradientBoostingClassifier` on the raw features (big jump expected)
- Standardize the numeric columns and add hillshade / distance interaction features
- Add elevation-band and aspect-derived features, keep the binary soil indicators
- Tune the boosting hyperparameters (`max_leaf_nodes`, `learning_rate`, `max_iter`)

## References

- [scikit-learn: Forest CoverType dataset](https://scikit-learn.org/stable/datasets/real_world.html#forest-covertypes)
- [scikit-learn: Histogram-based gradient boosting](https://scikit-learn.org/stable/modules/ensemble.html#histogram-based-gradient-boosting)
