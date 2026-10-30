#!/usr/bin/env bash
# One-time worker setup: download the REAL California Housing dataset once and cache it to
# ~/.sklearn_cache (on the instance EBS, survives stop/start). uv resolves deps on first run.
set -euo pipefail
cd "$(dirname "$0")/src"
uv run python -c "import os; from sklearn.datasets import fetch_california_housing; fetch_california_housing(data_home=os.path.expanduser('~/.sklearn_cache')); print('california_housing cached')"
