#!/usr/bin/env bash
# One-time worker setup: nothing to download. uv resolves deps (numpy) from src/pyproject.toml
# on the first `uv run`. Kept as a no-op so the bootstrap convention is satisfied.
set -euo pipefail
echo "circle-packing: no dataset to stage; deps resolved by uv on first run."
