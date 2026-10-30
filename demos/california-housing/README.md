# california-housing

A Kaggle-style tabular **regression** problem on the **real California Housing** benchmark (1990 census; the classic scikit-learn / StatLib dataset used across countless papers and tutorials). The agent edits `src/model.py` — `fit(X, y)` then `predict(X)` — to **maximize held-out R²** (`r2`, higher is better).

A plain linear-regression baseline (R² ~0.60) leaves large headroom: feature engineering (geographic interactions from lat/long, per-capita ratios, nonlinear transforms) plus a gradient-boosting model pushes R² toward ~0.85. Real data, pure CPU, ~seconds per evaluation — iterate on features and models across many experiments.

## Background

The real California Housing regression benchmark (scikit-learn `fetch_california_housing`): 20,640 rows, 8 numeric features — MedInc, HouseAge, AveRooms, AveBedrms, Population, AveOccup, Latitude, Longitude — with the target being median house value. The read-only evaluator downloads the real dataset (cached to `~/.sklearn_cache`), does a fixed 70/30 split (`random_state=0`), calls `model.fit(X_train, y_train)` then `model.predict(X_test)`, and reports held-out R².

The baseline `src/model.py` is an ordinary linear regression on the raw 8 features (R² ~0.60). **Run the baseline first** to establish the starting R², then try to beat it.

## What the agent optimizes

- **Edits `src/model.py`** — `fit(X, y)` (train on features + target) and `predict(X)` (return predictions). Feature engineering and model choice both live here.
- **Cannot edit `src/evaluate.py`, `src/pyproject.toml`** — the fixed 70/30 split, R² scoring, and environment. A failed/invalid candidate scores -1.0.
- **Metric:** `r2` (higher is better), from `run.log`.

## Ideas to try

- Run the linear-regression baseline on the raw 8 features to establish the starting R²
- Swap the model to `HistGradientBoostingRegressor` on the raw features (available and strong, R² ~0.83+)
- Add per-household ratios (AveRooms/AveOccup, AveBedrms/AveRooms) and a log(MedInc) feature
- Engineer geography: bin or KMeans-cluster (Latitude, Longitude), add lat*long and distance-to-coast features
- Clip/winsorize outliers in AveRooms/AveOccup/Population, then tune the boosting hyperparameters

## References

- [California Housing dataset (scikit-learn)](https://scikit-learn.org/stable/datasets/real_world.html#california-housing-dataset)
- [Histogram-based Gradient Boosting (scikit-learn)](https://scikit-learn.org/stable/modules/ensemble.html#histogram-based-gradient-boosting)
