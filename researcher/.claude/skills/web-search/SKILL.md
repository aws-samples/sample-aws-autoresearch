---
name: web-search
description: Search the web, arXiv and GitHub, and read pages/PDFs as plain text, via a local script. Use whenever a task needs web research (docs, papers, repos, news) and the built-in WebSearch/WebFetch tools are unavailable or unreliable -- e.g. when Claude Code runs through Amazon Bedrock.
---

# Web search (Bedrock-safe)

Claude Code on Amazon Bedrock cannot reliably use the built-in WebSearch/WebFetch tools. This skill uses a stdlib-only
script that calls public endpoints directly: DuckDuckGo HTML, the arXiv API, the GitHub API,
and plain HTTP fetches. PDFs are converted with `pdftotext` (poppler, installed on the researcher at boot).
`fetch` only accepts public http(s) URLs; `file://` and private/link-local addresses are refused.

Script: `~/.claude/skills/web-search/search.py` (run with `python3`).

## Commands

```bash
S=~/.claude/skills/web-search/search.py
python3 $S web    "low rank adaptation fine-tuning" -n 10     # title / url / snippet
python3 $S arxiv  "mixture of experts routing" -n 5          # id, date, authors, abstract
python3 $S github "flash attention" -n 5                      # repos (stars, last push)
python3 $S github "FlashAttention repo:pytorch/pytorch" --code   # code search (needs gh auth / GITHUB_TOKEN)
python3 $S fetch  https://arxiv.org/abs/2601.02078            # abs -> PDF -> text
python3 $S fetch  https://github.com/OWNER/REPO/blob/main/README.md  # blob -> raw
python3 $S fetch  <url> --start 20000 --max-chars 20000       # page through long docs
```

## How to research with it

1. Start broad with `web`, then narrow with `arxiv` (papers) and `github` (code).
2. Read the primary sources with `fetch`: the paper PDF, the README and docs, and the source
   files. Snippets alone aren't enough to rely on. Page through long documents with `--start`.
3. For a GitHub repo, prefer reading `README.md` and the docs folder with `fetch`
   (blob URLs are rewritten to raw). To list a tree, use
   `fetch https://api.github.com/repos/OWNER/REPO/contents/PATH`.
4. Cite the URLs you actually read, and say when something comes only from a search
   snippet.

## Notes

- DuckDuckGo can rate-limit bursts. If `web` returns nothing, wait a moment or rephrase.
- Queries go to third-party services, so don't put secrets or confidential identifiers in them.
- Anonymous GitHub API access is limited to ~60 requests/hour. `gh auth login` raises that limit and
  enables `--code`.
