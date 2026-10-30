#!/usr/bin/env bash
# Build the create_project Lambda's seed/ from the canonical sources. create_project publishes the
# whole seed to a shared S3 bundle (_platform/bundle/); each instance's pre-boot launcher syncs it
# into HOME (~/.claude/skills, ~/.auto-research), so the runtime is delivered out-of-band and the
# {project}-code repo stays clean.
#
#   seed/.claude/skills/<name>/SKILL.md + scripts/  <- researcher/.claude/skills (research-step, run-experiment)
#   seed/.claude/skills/knowledge-acquisition/      <- the LLM Wiki skill (SKILL.md + scripts)
#   seed/.claude/skills/web-search/                 <- Bedrock-safe web search + page reader (SKILL.md + search.py)
# NOTE: each skill MUST be a directory containing SKILL.md — Claude Code does not discover bare
# .claude/skills/<name>.md files (that yields "Unknown skill: <name>" at runtime).
#   seed/.auto-research/*.sh                         <- researcher/worker bootstrap, loop, log helpers
#
# seed/ is a BUILD ARTIFACT (gitignored). deploy.sh runs this before `cdk deploy`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SEED="$ROOT/infrastructure/lambda/create_project/seed"

rm -rf "$SEED"
mkdir -p "$SEED/.claude/skills" "$SEED/.auto-research"

# 1. All skills -> .claude/skills/ : the researcher skills (research-step, run-experiment) AND the
#    vendored knowledge-acquisition/ skill dir. Everything lives under researcher/.claude/skills/,
#    each as a <name>/SKILL.md directory (bare .md files are not discovered by Claude Code).
cp -R "$ROOT/researcher/.claude/skills/." "$SEED/.claude/skills/"
find "$SEED/.claude/skills" -type d \( -name '.venv' -o -name '__pycache__' -o -name 'node_modules' \) -exec rm -rf {} + 2>/dev/null || true

# 2. Worker bootstrap -> .auto-research/. The researcher boot/loop/dispatch/log-ship/agent-api are
#    now the `aws_auto_research` library (installed at boot); the researcher runs `aws-research
#    researcher boot` directly (see _user_data), so no researcher .sh are bundled here.
cp "$ROOT/worker/bootstrap.sh"               "$SEED/.auto-research/worker-bootstrap.sh"

# 3. The aws_auto_research runtime library -> seed/lib/ : pip-installed on the researcher at boot,
#    providing the `aws-research` CLI that the skills call directly.
mkdir -p "$SEED/lib"
cp "$ROOT/researcher/pyproject.toml" "$SEED/lib/"
cp -R "$ROOT/researcher/aws_auto_research" "$SEED/lib/"
find "$SEED/lib" -type d \( -name '.venv' -o -name '__pycache__' -o -name '*.egg-info' \) -exec rm -rf {} + 2>/dev/null || true

echo "Packaged $(find "$SEED" -type f | wc -l | tr -d ' ') files into $SEED"
