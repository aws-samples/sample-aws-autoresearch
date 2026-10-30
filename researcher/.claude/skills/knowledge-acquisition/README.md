# Knowledge Acquisition Skill

Build and maintain a persistent, compounding knowledge base as interlinked markdown files — an
**LLM Wiki**.

Unlike RAG (which rediscovers knowledge from scratch per query), the LLM Wiki compiles knowledge
once and keeps it current. Cross-references are pre-built, contradictions are flagged, and synthesis
reflects everything ingested. The wiki distills to state-of-the-art: it answers *"What is the best
way to do X right now?"* rather than cataloging every historical approach.

The skill is self-contained and fully autonomous: the wiki is a plain directory of markdown files, the
scripts need only Python and `uv`, and nothing depends on a web app or any hosting infrastructure.
Persisting or sharing the wiki (for example, as a git repository) is up to the caller.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (Python package runner; scripts declare their own dependencies)
- [gh](https://cli.github.com/) (GitHub CLI) — only for the GitHub search and clone scripts

## Wiki layout

```
$WIKI_DIR/                  # default: wiki/
├── SCHEMA.md               # Conventions, structure rules, tag taxonomy
├── index.md                # Sectioned content catalog with one-line summaries
├── log.md                  # Chronological action log (append-only)
├── raw/                    # Immutable source material (papers, articles, cloned code, assets)
├── entities/               # People, organizations, models
├── concepts/               # Concept / topic pages
├── comparisons/            # Side-by-side analyses
└── queries/                # Filed answers worth keeping
```

See [`SKILL.md`](SKILL.md) for the operating procedure (ingest, query, lint) and the full script
reference.

## Scripts

All scripts live in `scripts/` and are run with `uv run`. They write to `$WIKI_DIR/raw/...` by
default.

| Purpose | Scripts |
|---------|---------|
| Paper search | `search_papers.py` (arXiv + Semantic Scholar + OpenAlex), `search_arxiv.py`, `search_semantic_scholar.py`, `search_openalex.py`, `search_crossref.py`, `fetch_deepxiv.py` |
| Paper download | `download_papers.py`, `download_arxiv.py`, `download_semantic_scholar.py` |
| PDF processing | `pdf2md.py`, `extract_pdf.py`, `summarize_paper.py` |
| Code and implementations | `search_paperswithcode.py`, `search_github.py`, `search_github_code.py`, `clone_github_repo.py`, `fetch_github_readme.py`, `fetch_repo_metadata.py` |
| Reference | `fetch_wikipedia.py` |
| Utilities | `paper_db.py` (local paper index), `jsonl_to_bibtex.py`, `harvest_citations.py` |

Example:

```bash
export WIKI_DIR=wiki
SKILL_DIR=~/.claude/skills/knowledge-acquisition
uv run $SKILL_DIR/scripts/search_papers.py --query "mixture of experts routing" -o results.jsonl
uv run $SKILL_DIR/scripts/download_papers.py --jsonl results.jsonl --output-dir $WIKI_DIR/raw/papers/ --max-downloads 10
uv run $SKILL_DIR/scripts/pdf2md.py $WIKI_DIR/raw/papers/2101.03961.pdf
```

For general web search and reading arbitrary pages, use the companion `web-search` skill.

## Rate limits

| Source | Free rate limit | With API key |
|--------|----------------|--------------|
| arXiv | 1 req/3s | — |
| Semantic Scholar | ~100 req/5min | ~1 req/s (`S2_API_KEY`) |
| OpenAlex | 10 req/s | — (free) |
| CrossRef | ~50 req/s | — |
| Papers With Code | ~1 req/s | — |
| DeepXiv | auto-registers | higher with `DEEPXIV_TOKEN` |
| GitHub (`gh`) | 10 search/min | authenticated by default |

## License

MIT-0 — see [`LICENSE`](LICENSE).
