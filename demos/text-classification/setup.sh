#!/usr/bin/env bash
# One-time worker setup: download the REAL 20 Newsgroups dataset once, cache to ~/.sklearn_cache.
set -euo pipefail
cd "$(dirname "$0")/src"
uv run python -c "import os; from sklearn.datasets import fetch_20newsgroups; [fetch_20newsgroups(subset=s, data_home=os.path.expanduser('~/.sklearn_cache')) for s in ('train','test')]; print('20newsgroups cached')"
