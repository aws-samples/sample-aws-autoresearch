#!/usr/bin/env bash
# scientific-paper-writing/scripts/setup-latex.sh — install a LaTeX engine + stage the paper template.
#
# Installs Tectonic (a single arch-aware binary that fetches packages on demand); if that fails, falls
# back to a TeX Live subset via apt. Then copies the vendored IEEE conference template (IEEEtran.cls +
# IEEEtran.bst, LPPL-licensed, shipped with this skill) next to the paper sources so it builds offline.
# Idempotent. Overridable: $TECTONIC_VERSION.
set -uo pipefail

PAPER="$HOME/workspace/code/paper"
mkdir -p "$PAPER/figures"
mkdir -p "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"

# 1. LaTeX engine — Tectonic first (arch-aware static binary), else TeX Live via apt.
if ! command -v tectonic >/dev/null 2>&1; then
    case "$(uname -m)" in aarch64|arm64) ARCH=aarch64 ;; *) ARCH=x86_64 ;; esac
    VER="${TECTONIC_VERSION:-0.15.0}"
    URL="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${VER}/tectonic-${VER}-${ARCH}-unknown-linux-musl.tar.gz"
    if curl -fsSL "$URL" -o /tmp/tectonic.tgz 2>/dev/null && tar -xzf /tmp/tectonic.tgz -C "$HOME/.local/bin" tectonic 2>/dev/null; then
        chmod +x "$HOME/.local/bin/tectonic"
        echo "installed tectonic $VER ($ARCH)"
    else
        echo "tectonic download failed — falling back to TeX Live (apt)"
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            texlive-latex-recommended texlive-fonts-recommended texlive-latex-extra texlive-publishers latexmk >/dev/null 2>&1 \
            || echo "WARNING: no LaTeX engine could be installed"
    fi
fi
command -v tectonic >/dev/null 2>&1 && echo "engine: tectonic" || { command -v latexmk >/dev/null 2>&1 && echo "engine: latexmk (texlive)"; }

# 2. Stage the IEEE conference template (vendored with this skill) beside the paper sources so both
#    tectonic and pdflatex/bibtex resolve it locally — no network needed for the class/bib style.
TPL="$PAPER_DIR/templates"
for f in IEEEtran.cls IEEEtran.bst; do
    if [ -f "$TPL/$f" ]; then
        cp -f "$TPL/$f" "$PAPER/$f"
    else
        echo "WARNING: $TPL/$f missing — build may fail to find the IEEE class/bib style"
    fi
done
echo "setup-latex done (template: IEEEtran)"
