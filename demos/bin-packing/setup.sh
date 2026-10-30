#!/usr/bin/env bash
# One-time worker setup: download the REAL OR-Library Falkenauer U120 bin-packing instances once,
# cache to ~/.bpp/binpack1.txt (on EBS, survives stop/start).
set -euo pipefail
mkdir -p "$HOME/.bpp"
curl -fsSL --max-time 60 -o "$HOME/.bpp/binpack1.txt" \
  "https://people.brunel.ac.uk/~mastjjb/jeb/orlib/files/binpack1.txt"
echo "OR-Library binpack1 cached ($(wc -l < "$HOME/.bpp/binpack1.txt") lines)"
