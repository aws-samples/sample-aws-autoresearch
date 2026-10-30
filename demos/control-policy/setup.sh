#!/usr/bin/env bash
# One-time worker setup: no dataset to stage. uv resolves deps (gymnasium, numpy) from
# src/pyproject.toml on the first `uv run`.
set -euo pipefail
echo "control-policy: no dataset to stage; deps resolved by uv on first run."
