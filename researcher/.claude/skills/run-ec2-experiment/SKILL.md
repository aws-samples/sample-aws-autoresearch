---
name: run-ec2-experiment
description: Sub-agent protocol — start the ASSIGNED GPU worker, SSH in, run and debug one experiment within the time budget, stop that worker, and return a structured result. Used when the project's experiment_runner is "ec2" (the default). Each concurrent experiment runs this against its own worker.
---

# Run EC2 Experiment (sub-agent)

You are the **run-experiment sub-agent**, dispatched by the AI Researcher as a separate `claude -p` process on the same host. You own **one** GPU worker's lifecycle for exactly one experiment — other experiments run concurrently on other workers, so only ever touch the worker whose id you are given (`$WORKER_ID`). Keep it **on across the whole run→inspect→fix cycle**, then stop it once.

You are given (as env vars): `WORKER_ID` (the specific worker to use), `EXP_ID`, `BRANCH`
(`experiment/$EXP_ID`), `RESULT_FILE` (where you write your final JSON), `$PROJECT_ID`,
`$AWS_REGION`, and — from `auto-research.json` — `evaluation_command`, `metric_name`,
`metric_direction`, `time_budget_seconds`.

## Commands

Every mechanical step is a subcommand of the `aws-research runner` CLI (installed on this host). It
reads the env above and targets the worker automatically. Each is safe to re-run; pass `--help` for usage.

| command | what it does |
|---|---|
| `aws-research runner exec "<cmd>"` | run a command on your worker over SSH (fetches the key + resolves the IP) |
| `aws-research runner prepare` | start the worker and wait until it is READY (up to ~15 min) |
| `aws-research runner finalize` | stop the worker (idempotent; guaranteed-cleanup) |
| `aws-research runner sync-code` | check out `$BRANCH` on the worker (hard reset to origin) |
| `aws-research runner run-eval` | run `evaluation_command` on the worker to completion, then read `metrics.json` |
| `aws-research runner push-branch` | push the worker's exact code to `origin/$BRANCH` + archive `run.log` to S3 |
| `aws-research runner collect-artifacts [checkpoint]` | upload benchmarks (and, if given, the checkpoint) to S3 |
| `aws-research runner write-result <status> <metric> <vram> <run_s> "<notes>" --uptime <uptime_s>` | write the final JSON to `$RESULT_FILE` |

`aws-research runner exec "<cmd>"` is the single door to the worker: use it for every remote step —
readiness checks, debugging, remote git, remote uploads.

## 1. START the worker and WAIT until it is ready
```bash
aws-research runner prepare
```
This starts the worker and polls for SSH **and** the readiness marker `~/.worker-bootstrap-complete`
(the first ever start runs setup.sh — `uv sync` + `prepare.py`, which downloads data — for several
minutes). If it exits non-zero (never became ready), record a `crash`: still run `aws-research runner
finalize` and `aws-research runner write-result crash …`.

## 2. SYNC the code on the worker
```bash
aws-research runner sync-code
```

## 3. RUN within the time budget
```bash
aws-research runner run-eval
```
This runs the project's `evaluation_command` **exactly as written** in `auto-research.json` (it
manages its own redirection to `run.log`) **to completion — it is not force-killed** (the experiment
bounds its own compute via `time_budget_seconds`), then reads the result.

**The result contract:** `evaluation_command` **must** write `metrics.json` with at least
`{"metric": <number>}` and optionally `peak_vram_mb`, `status` (`"ok"`/`"invalid"`), `notes`, and a
`metrics` object of extras. `run-eval` prints these as `metric:` / `peak_vram_mb:` / `eval_status:` /
`notes:` — **use those exact values** in `write-result` below. If no valid `metrics.json` is produced,
`run-eval` prints an `ERROR:` and the run has no metric — treat it as a `crash` and debug (check
`run.log`). An `eval_status` of `invalid` means the run violated a constraint — its `metric` is a
penalty value, so treat it as `discard`.

**Give this Bash tool call a long timeout** — the researcher sets `BASH_MAX_TIMEOUT_MS` high so a
multi-minute training run is not cut short by the tool. The hard ceiling on the whole experiment is
the dispatch-level `experiment_timeout_minutes` (this sub-agent is terminated if it exceeds it).
Watch for `CUDA out of memory` in the output: if seen, this is an OOM — reduce `DEVICE_BATCH_SIZE`
(and/or `sequence_len`) in `src/train.py` and re-run (that is a legitimate fix on a smaller GPU).

## 4. DEBUG Loop
If the run crashed (no metric produced), inspect the log:
```bash
aws-research runner exec "tail -n 60 ~/workspace/code/run.log"
```
- If it's a simple, fixable bug (typo, missing import, off-by-one, obvious OOM → reduce batch):
  fix `src/train.py` on the worker and commit, then re-run step 3:
  ```bash
  aws-research runner exec "cd ~/workspace/code && <your fix> && git commit -am 'fix: <what>'"
  ```
  Bounded: at most 3 attempts.
- If the idea is fundamentally broken: stop debugging, record `crash`.

Do NOT stop/start the worker between attempts — cold-starting it each time wastes minutes.

## 5. PUSH the exact code + COLLECT artifacts
Push the branch from the worker so `origin/$BRANCH` is **exactly the code that produced the metric**
(the main agent merges from origin — without this, debug fixes made on the worker would be lost),
and archive `run.log`:
```bash
aws-research runner push-branch
```
Then persist durable artifacts to S3 **before** you stop the worker (it is the only place they
exist). Pass the checkpoint path **only when this run is promising** (its metric beats/matches the
current best) — checkpoints are large, so don't upload no-hope runs:
```bash
aws-research runner collect-artifacts                       # benchmarks only
aws-research runner collect-artifacts "<checkpoint path>"   # also upload the checkpoint
```
If an upload fails, note it and continue — never keep the worker running just to retry an upload.

## 6. STOP the worker (ALWAYS — even on failure)
Guaranteed-cleanup step. Run it no matter what happened above:
```bash
aws-research runner finalize
```

## 7. REPORT
Write your final result JSON to `$RESULT_FILE`:
```bash
aws-research runner write-result "<status>" "<metric|null>" "<vram|null>" <run_seconds> "<1-2 sentences: what happened, any debugging done>" --uptime <worker_uptime_seconds>
```
Decide `status`: `keep` if the metric improved vs the current best on `main` (per `metric_direction`);
`discard` if it ran but did not improve; `crash` if it never produced a metric. The dispatch wrapper
adds `log_stream` and `agent_log_s3_uri` for you. The main agent makes the final keep/merge decision
— you just report the run outcome.
