#!/usr/bin/env bash
# One-time worker setup: download REAL TSPLIB instances to ~/.tsplib (EBS).
set -euo pipefail
mkdir -p "$HOME/.tsplib"; cd "$HOME/.tsplib"
BASE="https://raw.githubusercontent.com/mastqe/tsplib/master"
for f in berlin52 eil51 eil76 kroA100 ch150; do
  curl -fsSL --max-time 40 -o "$f.tsp" "$BASE/$f.tsp"
done
echo "TSPLIB instances cached: $(ls ~/.tsplib | tr '\n' ' ')"
