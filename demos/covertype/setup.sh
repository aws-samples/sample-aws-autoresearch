#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/src"
uv run python -c "import os; from sklearn.datasets import fetch_covtype; fetch_covtype(data_home=os.path.expanduser('~/.sklearn_cache')); print('covtype cached')"
