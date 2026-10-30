#!/usr/bin/env bash
# One-time worker setup: no dataset to stage; the evaluator generates instances deterministically.
# uv resolves deps (numpy) from src/pyproject.toml on the first `uv run`.
set -euo pipefail
echo "tsp-heuristic: no dataset to stage; deps resolved by uv on first run."
