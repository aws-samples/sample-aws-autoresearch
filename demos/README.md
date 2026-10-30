# demos

Ready-to-upload **bootstrap directories** for the AI Researcher. Each subfolder fully defines one
research project; you upload it in the web app's *Create New Project* flow and it is committed to
the project's code repo to start the run.

The **bootstrap-directory convention** every folder here follows is documented in the
[repository README](../README.md#defining-a-project-the-bootstrap-directory-convention) — the main
user documentation.

All demos use **real data / real problem spaces** (no synthetic datasets) and are sized so one
experiment runs in well under 15 minutes, giving the agent room to run many experiments and
incrementally improve the metric. Provenance and licensing are summarized at the end of this file.

## Projects

Pick the **worker instance type** in the *Create New Project* form. Every demo runs on the smallest
instance, so start there.

### `g4dn.xlarge` (NVIDIA T4, 16 GB) — the default

The cheapest, most readily-available GPU instance. **All demos below are CPU-only** (they don't
need the GPU at all — they just run on the default worker). Each evaluator runs in seconds to a
couple of minutes.

**Kaggle-like ML on real datasets (feature engineering + model selection):**
- [`california-housing/`](./california-housing/) — tabular **regression** on the real scikit-learn
  California Housing benchmark; maximize held-out `r2` (linear baseline ~0.59 → ~0.87 with FE + GBM).
- [`magic-telescope/`](./magic-telescope/) — tabular **binary classification** on the real MAGIC
  Gamma Telescope benchmark (OpenML 1120); maximize `roc_auc` (~0.84 → ~0.95).
- [`covertype/`](./covertype/) — large-scale tabular **multiclass classification** on the real
  Forest CoverType benchmark (scikit-learn); maximize `accuracy` (~0.64 → ~0.95).
- [`superconductor/`](./superconductor/) — **materials-science regression** on the real UCI
  Superconductivity / NIMS SuperCon data: predict a material's critical temperature from its chemical
  composition; minimize held-out `rmse` in kelvin (linear baseline ~17.2 K → ~10 K with gradient
  boosting; published XGBoost reference ~9.5 K, Hamidieh 2018).
- [`text-classification/`](./text-classification/) — **text classification** on the real 20
  Newsgroups benchmark (scikit-learn); maximize `accuracy` (TF-IDF baseline ~0.67 → ~0.80 with n-gram
  / feature engineering).

**Algorithm & optimization discovery (pure CPU):**
- [`circle-packing/`](./circle-packing/) — pack 26 non-overlapping circles in the unit square to
  maximize the sum of radii (the DeepMind AlphaEvolve n=26 task); maximize `sum_radii`
  (grid baseline ~2.12 → AlphaEvolve reference ~2.635).
- [`bin-packing/`](./bin-packing/) — discover an online bin-packing heuristic (the DeepMind FunSearch
  task) on the real OR-Library Falkenauer U120 instances; minimize `mean_bins` (best-fit ~51.9 →
  optimum ~49.15).
- [`tsp-tsplib/`](./tsp-tsplib/) — TSP heuristic on the real **TSPLIB** instances (berlin52, eil51,
  eil76, kroA100, ch150) with published optima; minimize the optimality `gap_pct` (nearest-neighbour
  ~24% → a few %).
- [`tsp-heuristic/`](./tsp-heuristic/) — TSP heuristic on random Euclidean instances; minimize the
  average `tour_len` (nearest-neighbour ~9.4 → ~7.4 with 2-opt/Or-opt). Simpler, self-contained
  counterpart to `tsp-tsplib`.

**Control:**
- [`control-policy/`](./control-policy/) — discover a controller for the Gymnasium `Pendulum-v1`
  swing-up task; maximize the episode `return` (zero-torque ~ -1162 → ~ -150 with energy swing-up +
  PD stabilization).

> **Tip:** these CPU-only demos don't use the GPU — they run on the default `g4dn.xlarge` simply
> because that is Auto-Research's smallest worker option. They are the cheapest and quickest demos
> to run.

> Results from a given demo are only comparable **within the same worker type and budget**.
> Don't compare runs across different worker types.

## Provenance & licensing

All demo harnesses are original code written for this repository; no third-party research code is
vendored. Sources of the tasks and data:
- `circle-packing` and `bin-packing` reproduce the tasks from DeepMind's
  [AlphaEvolve](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
  and [FunSearch](https://www.nature.com/articles/s41586-023-06924-6) (using the public OR-Library
  bin-packing instances);
- `tsp-tsplib` uses the public [TSPLIB](http://comopt.ifi.uni-heidelberg.de/software/TSPLIB95/)
  instances; `control-policy` uses [Gymnasium](https://gymnasium.farama.org/);
- the tabular/text demos use the standard public benchmarks bundled with
  [scikit-learn](https://scikit-learn.org/stable/datasets/real_world.html) and
  [OpenML](https://www.openml.org/) (California Housing, Forest CoverType, 20 Newsgroups, MAGIC Gamma
  Telescope), and `superconductor` downloads the [UCI Superconductivity](https://archive.ics.uci.edu/dataset/464/superconductivty+data)
  dataset (CC BY 4.0).
