---
name: scientific-paper-writing
description: Write up this project's research as an IEEE-conference scientific paper (LaTeX → PDF, IEEEtran) from the wiki, the experiments ledger, the status report, and the code — then build the PDF, commit the sources, and upload the PDF + figures to S3. Runs once when the research is complete.
---

# Scientific Paper Writing

You are the **AI Researcher**, now writing up your completed research as an **IEEE-conference paper**
(the `IEEEtran` class, vendored with this skill). Assemble it from the durable artifacts you already
produced — **do not invent anything**. Every reported number must come from the experiments ledger or
an S3 artifact; every claim in Related Work must cite a real source from the wiki.

## Environment
 * **Working Directory**: `~/workspace/code`. The paper lives in `paper/` here.
 * `$PAPER_DIR`: this skill's directory (scripts + templates), preset in your environment.
 * `$PROJECT_ID`, `$AWS_REGION` are set. State access is via `aws-research api '<json>'`.

## Inputs (read these first)
- **LLM Wiki** (`~/workspace/wiki`) — concepts, techniques, and the literature you gathered. This is
  your **Related Work** and your **bibliography** source (`~/workspace/wiki/raw/papers.db.jsonl`).
- **Status Report** (`~/workspace/code/report/status.md`) — the narrative, best result, and learnings.
- **Experiments ledger** — the source of truth for results. Get it with:
  ```bash
  aws-research api "{\"action\":\"list_experiments\",\"project_id\":\"$PROJECT_ID\"}" \
    | jq '.experiments[] | {id:.experiment_id, state, metric:.metric_value, cost:.cost_usd, desc:.description, hypothesis}'
  ```
- **The winning code** — `main` branch of this repo (the method you are describing).
- **`auto-research.json`** — the objective (`metric_name`, `metric_direction`), setup, budget.

## Steps

### 1. Set up LaTeX + the template
```bash
bash "$PAPER_DIR/scripts/setup-latex.sh"
```
This installs a LaTeX engine (Tectonic, or TeX Live as fallback) and stages the **IEEE conference
template** — `IEEEtran.cls` + `IEEEtran.bst` are vendored with this skill (LPPL) and copied next to
the paper sources, so the paper builds offline with no template download.

Start the sources from the scaffold (only if `paper/main.tex` doesn't exist yet):
```bash
mkdir -p ~/workspace/code/paper/figures
cp -n "$PAPER_DIR/templates/main.tex" ~/workspace/code/paper/main.tex
cp -n "$PAPER_DIR/templates/refs.bib" ~/workspace/code/paper/refs.bib
```

### 2. Write the paper (edit `paper/main.tex` with the Edit tool)
Fill every section; keep it honest and specific:
- **Title / Abstract** — the problem + headline result (the best `metric_name`).
- **Introduction** — motivation and contributions (from the wiki + report).
- **Related Work** — synthesize the wiki's `#literature` pages; **cite** them via `\cite{...}` keys in
  `refs.bib`. Build `refs.bib` from your gathered sources:
  ```bash
  uv run ~/.claude/skills/knowledge-acquisition/scripts/jsonl_to_bibtex.py \
    --jsonl ~/workspace/wiki/raw/papers.db.jsonl --output ~/workspace/code/paper/refs.bib
  ```
- **Method** — the approach that produced the best result (describe the `main` code, not every dead end).
- **Experimental Setup** — dataset, `evaluation_command`, budget/compute (worker type), metric.
- **Results** — a table built **from the ledger** (one row per kept/notable experiment: change → metric),
  plus figures. Include the metric progression. **Numbers must match the ledger.**
- **Conclusion** — transferable takeaways (mirror the wiki's `#lesson` pages).

### 3. Figures (scripts in the repo, images to S3)
Write figure generators as `paper/figures/*.py` (matplotlib; they read the ledger and/or the report
charts in `$S3/report/`). `build-paper.sh` runs them to (re)create the PNGs at build time. Reference
them in LaTeX with relative paths, e.g. `\includegraphics{figures/kl_progress.png}`.

### 4. Build the PDF + upload
```bash
bash "$PAPER_DIR/scripts/build-paper.sh"
```
This regenerates the figures, builds `paper/paper.pdf`, and uploads `paper.pdf` + `figures/` to
`$S3/paper/`. If the build errors, read the LaTeX log, fix `main.tex`, and re-run (bounded: a few tries).

### 5. Commit the sources to the code repo
Keep the LaTeX **sources and figure scripts** versioned (the IEEEtran class comes from setup-latex.sh;
the generated images + PDF live in S3):
```bash
cd ~/workspace/code
git add paper/main.tex paper/refs.bib paper/figures/*.py 2>/dev/null
git commit -m "paper: scientific write-up for $PROJECT_ID" && git push
```

When the PDF is in `$S3/paper/paper.pdf`, the web app shows a **Paper** tab. End your turn.
