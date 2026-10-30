#!/usr/bin/env bash
# scientific-paper-writing/scripts/build-paper.sh — build paper/main.tex → PDF and upload to S3.
#
# 1. (re)generate figures by running paper/figures/*.py (they read the ledger / S3 report charts),
# 2. build the PDF with whatever engine is present (tectonic preferred, else latexmk/pdflatex),
# 3. upload paper.pdf + figures/ to $S3/paper/.
# Prints the PDF's S3 URI on success; exits non-zero if no PDF was produced.
set -uo pipefail

: "${PROJECT_ID:?PROJECT_ID must be set}" "${AWS_REGION:?AWS_REGION must be set}"
export PATH="$HOME/.local/bin:$PATH"
PAPER="$HOME/workspace/code/paper"
cd "$PAPER" 2>/dev/null || { echo "no paper/ directory — write paper/main.tex first" >&2; exit 1; }
[ -f main.tex ] || { echo "paper/main.tex not found" >&2; exit 1; }

# 1. Figures — run generator scripts (idempotent; they write PNGs into figures/).
mkdir -p figures
for f in figures/*.py; do
    [ -f "$f" ] || continue
    echo "figure: $f"
    python3 "$f" || echo "  (figure $f failed — continuing)"
done

# 2. Build.
echo "building PDF ..."
if command -v tectonic >/dev/null 2>&1; then
    tectonic -X compile main.tex --outdir . >/tmp/paper-build.log 2>&1 || { echo "tectonic build failed:"; tail -n 30 /tmp/paper-build.log; exit 1; }
elif command -v latexmk >/dev/null 2>&1; then
    latexmk -pdf -interaction=nonstopmode main.tex >/tmp/paper-build.log 2>&1 || { echo "latexmk build failed:"; tail -n 30 /tmp/paper-build.log; exit 1; }
else
    { pdflatex -interaction=nonstopmode main.tex && (bibtex main || true) \
        && pdflatex -interaction=nonstopmode main.tex && pdflatex -interaction=nonstopmode main.tex; } \
        >/tmp/paper-build.log 2>&1 || { echo "pdflatex build failed:"; tail -n 30 /tmp/paper-build.log; exit 1; }
fi
[ -f main.pdf ] || { echo "build produced no main.pdf" >&2; tail -n 30 /tmp/paper-build.log; exit 1; }
cp -f main.pdf paper.pdf

# 3. Upload PDF + figures to S3.
S3=$(aws-research api "{\"action\":\"get_project\",\"project_id\":\"$PROJECT_ID\"}" | jq -r '.project.s3_data')
aws s3 cp paper.pdf "$S3/paper/paper.pdf" --region "$AWS_REGION" >/dev/null && echo "uploaded $S3/paper/paper.pdf"
[ -n "$(ls figures/*.png 2>/dev/null)" ] && aws s3 cp figures/ "$S3/paper/figures/" --recursive --exclude "*" --include "*.png" --region "$AWS_REGION" >/dev/null 2>&1 && echo "uploaded figures to $S3/paper/figures/"
echo "build-paper done -> $S3/paper/paper.pdf"
