# superconductor

Predict the **critical temperature** (Tc) of real superconducting materials from their **chemical
composition** — the UCI *Superconductivity* benchmark (Hamidieh, 2018), built from the NIMS
**SuperCon** database of ~21k measured superconductors. The agent edits `src/model.py` — `fit(X, y)`
then `predict(X)` — to **minimize held-out RMSE in kelvin** (`rmse`, lower is better).

A plain linear regression on the paper's 81 composition descriptors (RMSE ~17.2 K, R² ~0.74) leaves
large headroom; gradient boosting gets to ~10 K. The published reference is **Hamidieh's XGBoost:
RMSE ~9.5 K, R² ~0.92**, and later papers on the same 81 features report ~9.3–9.7 K — so getting
clearly **below ~9.5 K** under this protocol is a genuine result worth writing up. Real data, pure CPU,
seconds per evaluation, so you can iterate across many experiments.

## Background

Superconductors carry current with zero resistance below a critical temperature Tc. Predicting Tc
from composition alone is an open materials-science problem — there is no accepted theory that does
it for unconventional (e.g. cuprate, iron-based) superconductors, so data-driven models are used to
screen candidate materials before costly synthesis.

The dataset (UCI id 464, CC BY 4.0) has **21,263 rows**, one per measured material (15,542 unique
chemical formulas — some materials were measured more than once). `src/data.py` (read-only) downloads
it once and hands the model a pandas DataFrame with, in order:

- `material` — the chemical formula string (e.g. `Ba0.2La1.8Cu1O4`, `Fe1Se1`, `Mg1B2`);
- **81 descriptors** from the paper: `number_of_elements`, then the mean, weighted mean, geometric
  mean (plain/weighted), entropy (plain/weighted), range (plain/weighted), and standard deviation
  (plain/weighted) of 8 elemental properties — atomic mass, first ionization energy, atomic radius,
  density, electron affinity, fusion heat, thermal conductivity, and valence;
- **86 element amounts** `H` … `Rn` — the stoichiometric coefficients parsed from the formula.

The read-only evaluator does a fixed **2/3–1/3 train/test split grouped by chemical formula**
(`GroupShuffleSplit`, `random_state=0`) so the same material never appears in both train and test,
calls `model.fit(X_train, y_train)` then `model.predict(X_test)`, and reports held-out **RMSE** (plus
R² and MAE as secondary metrics). Note this differs slightly from the paper, which averaged 25 random
(ungrouped) splits; on this data the effect of grouping is small (~0.1 K for gradient boosting), but
it is the more honest protocol.

**Run the baseline first** to establish the reference RMSE, then try to beat it.

## What the agent optimizes

- **Edits `src/model.py`** — `fit(X, y)` and `predict(X)`; feature engineering, model choice,
  target transforms, and ensembling all live here.
- **Cannot edit `src/evaluate.py`, `src/data.py`, `src/pyproject.toml`** — the fixed dataset, grouped
  split, RMSE scoring, and environment (numpy, pandas, scikit-learn). A failed/invalid candidate
  scores a 1000 K penalty.
- **Metric:** `rmse` in kelvin (lower is better), written to `metrics.json` by the evaluator.
- **Rules:** learn only from the `X, y` passed to `fit` — don't load the dataset files yourself or
  otherwise look up test labels. Keep `fit` + `predict` within the project's training time budget
  (e.g. size ensembles / hyperparameter searches to fit it) so results stay comparable.

## Ideas to try

- Run the linear-regression baseline on the 81 descriptors to establish the starting RMSE (~17.2 K).
- Swap to `HistGradientBoostingRegressor` and add the 86 element amounts as features (~9.8 K).
- Use chemistry: flag material families from the formula (cuprates = contains Cu and O; iron-based =
  Fe with As/Se/P; hydrides; MgB2-like borides) — Stanev et al. found family-specific models help.
- Normalize element amounts to atomic fractions; add oxygen content / Cu–O ratios for cuprates.
- Model `log1p(Tc)` (the target is heavily skewed — most materials are below 10 K) and invert.
- Tune the boosting hyperparameters (depth, learning rate, iterations, min samples per leaf, L2).
- Ensemble diverse models (boosting + random forest / extra-trees + kNN) or stack them.

## References

- [Hamidieh, 2018 — A data-driven statistical model for predicting the critical temperature of a superconductor (arXiv:1803.10260)](https://arxiv.org/abs/1803.10260) — the dataset paper; XGBoost RMSE ~9.5 K, R² ~0.92; multiple regression ~17.6 K, 0.74.
- [UCI Machine Learning Repository — Superconductivity Data (id 464)](https://archive.ics.uci.edu/dataset/464/superconductivty+data)
- [Stanev et al., 2018 — Machine learning modeling of superconducting critical temperature (arXiv:1709.02727)](https://arxiv.org/abs/1709.02727) — family-specific models (cuprate / iron-based / low-Tc).
- [Shams et al., 2023 — ML approaches for estimating the critical temperature of superconductors (arXiv:2308.01932)](https://arxiv.org/abs/2308.01932) — stacking ensemble, RMSE 9.69 K, R² 0.922 on the 81 features.
- [Hu et al., 2022 — Prediction of superconducting properties of materials based on ML models (arXiv:2211.03075)](https://arxiv.org/abs/2211.03075) — deep forest RMSE 7.51 K, but on a re-cleaned 13.6k-row subset (not directly comparable).
- [NIMS SuperCon database](https://supercon.nims.go.jp/)
