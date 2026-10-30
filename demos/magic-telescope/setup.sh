#!/usr/bin/env bash
# One-time worker setup: download the REAL MAGIC Gamma Telescope dataset (OpenML 1120) once and
# cache it to ~/.sklearn_cache (on EBS, survives stop/start). uv resolves deps on first run.
set -euo pipefail
cd "$(dirname "$0")/src"
uv run python -c "import os; from sklearn.datasets import fetch_openml; fetch_openml(data_id=1120, as_frame=True, data_home=os.path.expanduser('~/.sklearn_cache')); print('magic-telescope cached')"
