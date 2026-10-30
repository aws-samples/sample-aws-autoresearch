#!/usr/bin/env bash
# One-time worker setup: download the REAL UCI Superconductivity dataset once and cache it to
# ~/.cache/superconductivity (on the instance EBS, survives stop/start). uv resolves deps on first run.
set -euo pipefail
cd "$(dirname "$0")/src"
uv run python -c "import data; data.load(); print('superconductivity cached')"
