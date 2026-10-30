---
name: knowledge-acquisition
description: "LLM Wiki: build and maintain a persistent, compounding knowledge base as interlinked markdown files. Includes tools to convert PDF files into Markdown format, and gather sources from arXiv, Semantic Scholar, OpenAlex, CrossRef, Papers With Code, Wikipedia, and GitHub."
compatibility: Requires Python 3.10+, uv, and gh (GitHub CLI) for GitHub scripts. Works fully offline from any hosting; the wiki is a plain directory of markdown files.
---

# LLM Wiki

Build and maintain a persistent, compounding knowledge base as interlinked markdown files.

Unlike RAG (which rediscovers knowledge from scratch per query), the wiki compiles knowledge
once and keeps it current. Cross-references are already there. Contradictions have already
been flagged. Synthesis reflects everything ingested.

First gather raw sources of knowledge, then summarize, cross-reference, file, and maintain consistency.

## Key Principle: Distill to State-of-the-Art

The wiki is not an archive of everything ever written on a topic. It is a **distilled, current view of the best known approaches**. When writing or updating wiki pages:

- **Keep pages small and focused** — each page should capture the current best approach to one concept, not a historical survey. Target ~50–150 lines.
- **Obsolete methods get removed, not accumulated.** If a newer paper supersedes an older technique, update the page to reflect the new state-of-the-art. Mention the predecessor briefly for context, not in detail.
- **Prioritize recency and impact.** When multiple approaches exist, lead with the one that has the best results or widest adoption *today*. Don't give equal weight to a 2019 method and a 2025 method that dominates it.
- **Archive aggressively.** If an entire page becomes obsolete (e.g., a technique fully superseded), move it to `_archive/` rather than letting it clutter the wiki.
- **The wiki should answer: "What is the best way to do X right now?"** — not "What are all the ways people have tried X since 2017?"

## When This Skill Activates

This skill is used by an autonomous agent: there is no human in the loop to ask for clarification, so
make decisions yourself and record your reasoning in the wiki. Use it when you need to:
- Research a topic, find papers, or gather sources
- Create or start the wiki (if `$WIKI_DIR` does not exist yet)
- Ingest, add, or process a source into the wiki
- Answer a question when a wiki is present (read it before searching the web)
- Lint, audit, or health-check the wiki

## Architecture: Three Layers

The wiki directory name is configurable — it does NOT have to be `wiki/`. Set `WIKI_DIR` to the wiki's root directory. The default is `wiki/`.

```bash
# Set WIKI_DIR to the wiki's root directory (default: wiki/)
export WIKI_DIR=wiki  # or: llm-wiki, security-playbook, etc.
```

```
$WIKI_DIR/
├── SCHEMA.md           # Conventions, structure rules, domain config
├── index.md            # Sectioned content catalog with one-line summaries
├── log.md              # Chronological action log (append-only)
├── raw/                # Layer 1: Immutable source material
│   ├── papers.db.jsonl # Local paper index
│   ├── papers/         # Downloaded PDFs and LaTeX sources
│   ├── articles/       # Web articles, clippings
│   ├── code/           # Cloned GitHub repositories
│   └── assets/         # Images, diagrams
├── entities/           # Layer 2: Entity pages (people, orgs, models)
├── concepts/           # Layer 2: Concept/topic pages
├── comparisons/        # Layer 2: Side-by-side analyses
└── queries/            # Layer 2: Filed query results worth keeping
```

**Layer 1 — Raw Sources (`raw/`):** Immutable. The agent reads but never modifies these.
**Layer 2 — The Wiki:** Agent-owned markdown files. Created, updated, and cross-referenced by the agent.
**Layer 3 — The Schema:** `SCHEMA.md` defines structure, conventions, and tag taxonomy.

## Core Operations

### 1. Ingest

When you are given a source or need to research a topic:

① **Gather raw sources** using the acquisition scripts (see below). Start with a web search (the `web-search` skill) to discover relevant blogs, articles, and leads — then narrow to academic sources with the scripts below.
② **Decide the takeaways** — what is interesting, what matters for the task at hand, and what is only background.
③ **Check what already exists** — read `index.md`, search for existing pages.
④ **Write or update wiki pages** — create entity/concept pages, add cross-references (`[[wikilinks]]`), use tags from the taxonomy.
⑤ **Update navigation** — add to `index.md`, append to `log.md`.
⑥ **Record what changed** — list every file created or updated in the `log.md` entry.

### 2. Query

When you need to answer a question:

① Read `index.md` to find relevant pages.
② Read the relevant pages.
③ Synthesize an answer with citations to wiki pages.
④ File valuable answers back as new pages in `queries/` or `comparisons/`.

### 3. Lint

Periodically health-check the wiki: orphan pages, broken wikilinks, stale content, contradictions, missing cross-references, index completeness.

## Source Acquisition Scripts

Scripts live in this skill's `scripts/` directory. They use PEP 723 inline dependencies resolved automatically by `uv run`.

**Important:** Always run scripts from the **project directory** (where the wiki lives), not from the skill directory. Use the full path to the script:

```bash
# SKILL_DIR is the directory containing this SKILL.md file.
# The agent should resolve this from the skill's installed location, e.g.:
#   ~/.claude/skills/knowledge-acquisition/scripts/

# WIKI_DIR is the wiki's root directory (default: wiki/)
# Set this to match the wiki name you're working with.
export WIKI_DIR=wiki  # or: llm-wiki, my-research, etc.

uv run $SKILL_DIR/scripts/<script>.py [args...]
```

All default output paths use `$WIKI_DIR/raw/papers/`, `$WIKI_DIR/raw/code/`, etc. Scripts accept `--output-dir` to override. When using the default, ensure `WIKI_DIR` points to the correct wiki directory.

### Wiki Naming

When creating a wiki, choose a **slug-like name**: lowercase, hyphens, no spaces. Examples:
- `wiki` — default for single-wiki projects
- `llm-wiki` — research on LLMs
- `security-playbook` — security knowledge base
- `project-alpha-kb` — project-specific knowledge

The name is the local directory name (e.g., `./llm-wiki/`).

### Typical Workflow

```bash
# Set SKILL_DIR to the absolute path of this skill's installation directory.
# (The agent should derive this from where it found SKILL.md)
# Set WIKI_DIR to the wiki's root directory.
export WIKI_DIR=wiki

# 0. Discover — use the web-search skill to find relevant sources, blogs, and leads
#    (python3 ~/.claude/skills/web-search/search.py web "<query>") before narrowing to academic sources

# 1. Orient — get background
uv run $SKILL_DIR/scripts/fetch_wikipedia.py summary "Mixture of Experts"

# 2. Search — find papers across multiple sources
uv run $SKILL_DIR/scripts/search_papers.py --query "mixture of experts routing" -o results.jsonl

# 3. Download — get the PDFs (saves to $WIKI_DIR/raw/papers/)
uv run $SKILL_DIR/scripts/download_papers.py --jsonl results.jsonl --output-dir $WIKI_DIR/raw/papers/ --max-downloads 10

# 4. Extract — convert to readable text
uv run $SKILL_DIR/scripts/pdf2md.py $WIKI_DIR/raw/papers/2101.03961.pdf

# 5. Find implementations (saves to $WIKI_DIR/raw/code/)
uv run $SKILL_DIR/scripts/clone_github_repo.py --repo google/flaxformer --output-dir $WIKI_DIR/raw/code/

# 6. Track what you've collected
uv run $SKILL_DIR/scripts/paper_db.py add --jsonl results.jsonl --db $WIKI_DIR/raw/papers.db.jsonl
```

### Rate Limits

| Source | Free rate limit | With API key |
|--------|----------------|--------------|
| arXiv | 1 req/3s | — |
| Semantic Scholar | ~100 req/5min | ~1 req/s (`S2_API_KEY`) |
| OpenAlex | 10 req/s | — (free) |
| CrossRef | ~50 req/s | — |
| Papers With Code | ~1 req/s | — |
| DeepXiv | auto-registers | higher with `DEEPXIV_TOKEN` |
| GitHub (`gh`) | 10 search/min | authenticated by default |

---

## Script Reference

### Paper Search

#### Unified Search (`search_papers.py`)

Search across arXiv, Semantic Scholar, and OpenAlex in one command with deduplication.

```bash
uv run $SKILL_DIR/scripts/search_papers.py --query "mixture of experts" --sources arxiv,s2,openalex -o results.jsonl
uv run $SKILL_DIR/scripts/search_papers.py --query "LLM reasoning" --sources arxiv,s2 --max-results 30
```

Options: `--query`, `--sources` (default: all), `--max-results` (default: 30), `--min-citations`, `--api-key`, `-o`.

#### arXiv Search (`search_arxiv.py`)

```bash
uv run $SKILL_DIR/scripts/search_arxiv.py --query "LLM agent" --categories cs.AI cs.CL --sort-by lastUpdatedDate -o results.jsonl
```

Options: `--query`, `--max-results`, `--categories`, `--sort-by`, `--start-date`, `--end-date`, `-o`.

#### Semantic Scholar Search (`search_semantic_scholar.py`)

```bash
uv run $SKILL_DIR/scripts/search_semantic_scholar.py --query "sparse MoE" --min-citations 50 -o results.jsonl
uv run $SKILL_DIR/scripts/search_semantic_scholar.py --citations-of "ARXIV:2101.03961" -o citing.jsonl
uv run $SKILL_DIR/scripts/search_semantic_scholar.py --references-of "ARXIV:2101.03961" -o refs.jsonl
uv run $SKILL_DIR/scripts/search_semantic_scholar.py --related-to "ARXIV:2101.03961" -o related.jsonl
```

Options: `--query`, `--max-results`, `--min-citations`, `--year-range`, `--venue`, `--citations-of`, `--references-of`, `--related-to`, `--api-key`, `-o`.

#### OpenAlex Search (`search_openalex.py`)

Free, no API key, 250M+ works.

```bash
uv run $SKILL_DIR/scripts/search_openalex.py --query "attention mechanism" --max-results 50 -o results.jsonl
```

Options: `--query`, `--max-results`, `--min-citations`, `--year-range`, `--type`, `--sort`, `-o`.

#### CrossRef Search (`search_crossref.py`)

Find DOI-registered publications, generate BibTeX.

```bash
uv run $SKILL_DIR/scripts/search_crossref.py --query "attention is all you need" --bibtex --output refs.bib
```

Options: `--query`, `--rows`, `--output`, `--bibtex`.

### Paper Download

#### Generic Download (`download_papers.py`)

Downloads PDFs from any search JSONL output (reads `pdf_url` field).

```bash
uv run $SKILL_DIR/scripts/download_papers.py --jsonl results.jsonl --max-downloads 10 --sort-by-citations
```

Options: `--jsonl`, `--output-dir` (default: `$WIKI_DIR/raw/papers/`), `--max-downloads`, `--delay`, `--sort-by-citations`.

#### arXiv Download (`download_arxiv.py`)

```bash
uv run $SKILL_DIR/scripts/download_arxiv.py --arxiv-id 2101.03961
uv run $SKILL_DIR/scripts/download_arxiv.py --title "Attention Is All You Need"
uv run $SKILL_DIR/scripts/download_arxiv.py --arxiv-id 2101.03961 --format source
```

Options: `--arxiv-id`, `--title`, `--format` (pdf/source), `--output-dir` (default: `$WIKI_DIR/raw/papers/`).

#### Semantic Scholar Download (`download_semantic_scholar.py`)

```bash
uv run $SKILL_DIR/scripts/download_semantic_scholar.py --paper-id "ARXIV:2101.03961"
uv run $SKILL_DIR/scripts/download_semantic_scholar.py --jsonl results.jsonl --max-downloads 10
```

Options: `--paper-id`, `--jsonl`, `--output-dir` (default: `$WIKI_DIR/raw/papers/`), `--max-downloads`, `--delay`.

### PDF Processing

#### PDF to Markdown (`pdf2md.py`)

```bash
uv run $SKILL_DIR/scripts/pdf2md.py $WIKI_DIR/raw/papers/2101.03961.pdf
```

Options: `--margins LEFT TOP RIGHT BOTTOM`.

#### Extract PDF Text (`extract_pdf.py`)

Structured extraction with section detection.

```bash
uv run $SKILL_DIR/scripts/extract_pdf.py --pdf $WIKI_DIR/raw/papers/2101.03961.pdf --sections-only
uv run $SKILL_DIR/scripts/extract_pdf.py --pdf-dir $WIKI_DIR/raw/papers/ --output-dir texts/
```

Options: `--pdf`, `--pdf-dir`, `--output-dir`, `--sections-only`.

#### Summarize Paper (`summarize_paper.py`)

Outputs structured JSON (title, abstract, sections array).

```bash
uv run $SKILL_DIR/scripts/summarize_paper.py $WIKI_DIR/raw/papers/2101.03961.pdf -o paper.json
```

Options: `pdf` (positional), `-o`, `--sections-only`.

### Code & Implementations

#### Papers With Code (`search_paperswithcode.py`)

Find GitHub repos linked to papers (searches GitHub for repos referencing arXiv IDs in their READMEs).

```bash
uv run $SKILL_DIR/scripts/search_paperswithcode.py --arxiv-id 2101.03961 --output repos.jsonl
uv run $SKILL_DIR/scripts/search_paperswithcode.py --query "multi-agent" --output repos.jsonl
```

Options: `--arxiv-id`, `--arxiv-ids`, `--arxiv-ids-file`, `--query`, `--output`.

#### GitHub Search (`search_github.py`)

```bash
uv run $SKILL_DIR/scripts/search_github.py --query "mixture of experts" --language Python --min-stars 100 --output repos.jsonl
```

Options: `--query`, `--output`, `--language`, `--min-stars`, `--sort`, `--max-results`, `--topic`.

#### GitHub Code Search (`search_github_code.py`)

Search code snippets across GitHub.

```bash
uv run $SKILL_DIR/scripts/search_github_code.py --query "FlashAttention" --language Python --output results.jsonl
```

Options: `--query`, `--output`, `--language`, `--filename`, `--max-results`.

#### Clone Repository (`clone_github_repo.py`)

```bash
uv run $SKILL_DIR/scripts/clone_github_repo.py --repo google/flaxformer
uv run $SKILL_DIR/scripts/clone_github_repo.py --repo https://github.com/google/flaxformer.git
```

Options: `--repo` (owner/name or full GitHub URL), `--output-dir` (default: `$WIKI_DIR/raw/code/`), `--depth`, `--branch`.

#### Fetch README (`fetch_github_readme.py`)

Get README without cloning.

```bash
uv run $SKILL_DIR/scripts/fetch_github_readme.py --repos pytorch/pytorch --output readmes.jsonl
```

Options: `--repos`, `--input`, `--output`, `--max-chars`.

#### Fetch Repo Metadata (`fetch_repo_metadata.py`)

```bash
uv run $SKILL_DIR/scripts/fetch_repo_metadata.py --repos pytorch/pytorch --output metadata.jsonl
```

Options: `--repos`, `--input`, `--output`, `--delay`.

### Reference & Context

#### Wikipedia (`fetch_wikipedia.py`)

```bash
uv run $SKILL_DIR/scripts/fetch_wikipedia.py summary "Transformer (machine learning model)"
uv run $SKILL_DIR/scripts/fetch_wikipedia.py sections "Gradient descent"
uv run $SKILL_DIR/scripts/fetch_wikipedia.py section "Gradient descent" --index 3
```

Subcommands: `summary`, `sections`, `section --index N`, `wikitext`.

#### DeepXiv Semantic Search (`fetch_deepxiv.py`)

Semantic (not keyword) paper search with progressive reading. Requires `deepxiv-sdk`.

```bash
uv run $SKILL_DIR/scripts/fetch_deepxiv.py search "low rank adaptation" --limit 10
uv run $SKILL_DIR/scripts/fetch_deepxiv.py brief 2106.09685
uv run $SKILL_DIR/scripts/fetch_deepxiv.py section 2106.09685 Introduction
```

Subcommands: `search`, `brief`, `head`, `section`, `raw`, `trending`, `social`.

### Utilities

#### JSONL to BibTeX (`jsonl_to_bibtex.py`)

Convert any search output to `.bib`.

```bash
uv run $SKILL_DIR/scripts/jsonl_to_bibtex.py --jsonl results.jsonl --output refs.bib
```

#### Paper Database (`paper_db.py`)

Local index to track collected papers and avoid re-downloads.

```bash
uv run $SKILL_DIR/scripts/paper_db.py add --jsonl results.jsonl
uv run $SKILL_DIR/scripts/paper_db.py list
uv run $SKILL_DIR/scripts/paper_db.py search --query "attention"
uv run $SKILL_DIR/scripts/paper_db.py has --arxiv-id 2301.07041
```

Options: `--db` (default: `$WIKI_DIR/raw/papers.db.jsonl`).

#### Harvest Citations (`harvest_citations.py`)

Scan LaTeX for uncited claims, find candidates via Semantic Scholar.

```bash
uv run $SKILL_DIR/scripts/harvest_citations.py --tex main.tex --bib references.bib --output candidates.bib
uv run $SKILL_DIR/scripts/harvest_citations.py --tex main.tex --bib references.bib --dry-run
```

Options: `--tex`, `--bib`, `--output`, `--max-rounds`, `--api-key`, `--dry-run`, `--verbose`.

## Pitfalls

- **Never modify files in `raw/`** — sources are immutable. Corrections go in wiki pages.
- **Always orient first** — read SCHEMA + index + recent log before any operation.
- **Always update `index.md` and `log.md`** — they are the navigational backbone.
- **Don't create pages for passing mentions** — only when an entity appears in 2+ sources or is central to one.
- **Every page needs cross-references** — minimum 2 outbound `[[wikilinks]]`.
- **Tags must come from the taxonomy** in SCHEMA.md.
- **JSONL is the interchange format** — search scripts output JSONL that download scripts consume.
