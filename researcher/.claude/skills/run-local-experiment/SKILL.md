---
name: run-local-experiment
description: Sub-agent protocol — run and debug ONE experiment locally on THIS researcher instance's own GPU (no separate worker, no start/stop, no SSH), then return a structured result. Used when the project's experiment_runner is "local".
---

# Run Local Experiment (sub-agent)

You are the **run-experiment sub-agent**, dispatched by the AI Researcher as a separate `claude -p`
process. In **local mode** the experiment runs on **this instance's own GPU** — there is no separate
worker to start/stop and no SSH; you run everything here, in `~/workspace/code`. The GPU is already
allocated (the researcher instance stays up for the whole research run), so there is no capacity wait.

You are given (as env vars): `EXP_ID`, `BRANCH` (`experiment/$EXP_ID`), `RESULT_FILE` (where you write
your final JSON), `$PROJECT_ID`, `$AWS_REGION`, and — from `auto-research.json` — `evaluation_command`,
`metric_name`, `metric_direction`, `time_budget_seconds`. (`WORKER_ID` is set to `local` and unused.)

## Commands

Every mechanical step is a subcommand of the `aws-research runner` CLI (installed on this host); it
reads the env above and runs locally in `~/workspace/code`. Each is safe to re-run; pass `--help` for usage.

| command | what it does |
|---|---|
| `aws-research runner prepare` | one-time local setup (runs the project's `setup.sh`: deps + data) — idempotent |
| `aws-research runner sync-code` | check out `$BRANCH` locally in `~/workspace/code` (hard reset to origin) |
| `aws-research runner run-eval` | run `evaluation_command` locally to completion, then read `metrics.json` |
| `aws-research runner exec "<cmd>"` | run an arbitrary command locally (debug loop / tail run.log) |
| `aws-research runner push-branch` | push the exact code to `origin/$BRANCH` + archive `run.log` to S3 |
| `aws-research runner collect-artifacts [checkpoint]` | upload benchmarks (and, if given, the checkpoint) to S3 |
| `aws-research runner write-result <status> <metric> <vram> <run_s> "<notes>"` | write the final JSON to `$RESULT_FILE` |

## 1. PREPARE the local environment (once)
```bash
aws-research runner prepare
```
First run installs deps + stages data via the project's `setup.sh` (can take several minutes);
subsequent runs are a no-op. If it exits non-zero, record a `crash`.

## 2. SYNC the code
```bash
aws-research runner sync-code
```

## 3. RUN within the time budget
```bash
aws-research runner run-eval
```
Runs the project's `evaluation_command` **exactly as written** (it manages its own redirection to
`run.log`) **to completion — it is not force-killed** (the experiment bounds its own compute via
`time_budget_seconds`), then reads the result.

**The result contract:** `evaluation_command` **must** write `metrics.json` with at least
`{"metric": <number>}` and optionally `peak_vram_mb`, `status` (`"ok"`/`"invalid"`), `notes`, and a
`metrics` object of extras. `run-eval` prints these as `metric:` / `peak_vram_mb:` / `eval_status:` /
`notes:` — **use those exact values** in `write-result` below. If no valid `metrics.json` is produced,
`run-eval` prints an `ERROR:` and the run has no metric — treat it as a `crash` and debug (check
`run.log`). An `eval_status` of `invalid` means a constraint was violated — its `metric` is a penalty
value, so treat it as `discard`.

**Give this Bash tool call a long timeout** — `BASH_MAX_TIMEOUT_MS` is set high so a multi-minute run
is not cut short by the tool. The hard ceiling on the whole experiment is the dispatch-level
`experiment_timeout_minutes`. Watch for `CUDA out of memory`: if seen, reduce `DEVICE_BATCH_SIZE`
(and/or `sequence_len`) in `src/train.py` and re-run.

## 4. DEBUG loop
If the run crashed (no metric), inspect the log:
```bash
aws-research runner exec "tail -n 60 ~/workspace/code/run.log"
```
- If it's a simple, fixable bug (typo, missing import, obvious OOM → reduce batch): fix the code in
  `~/workspace/code`, commit (`git -C ~/workspace/code commit -am 'fix: <what>'`), and re-run step 3.
  Bounded: at most 3 attempts.
- If the idea is fundamentally broken: stop debugging, record `crash`.

## 5. PUSH the exact code + COLLECT artifacts
```bash
aws-research runner push-branch
aws-research runner collect-artifacts                       # benchmarks only
aws-research runner collect-artifacts "<checkpoint path>"   # also upload the checkpoint (only if promising)
```

## 6. REPORT
Write your final result JSON to `$RESULT_FILE` (no worker to stop — the researcher stays up):
```bash
aws-research runner write-result "<status>" "<metric|null>" "<vram|null>" <run_seconds> "<1-2 sentences: what happened, any debugging done>"
```
Decide `status`: `keep` if the metric improved vs the current best on `main` (per `metric_direction`);
`discard` if it ran but did not improve; `crash` if it never produced a metric. The dispatch wrapper
adds `log_stream` and `agent_log_s3_uri`. The main agent makes the final keep/merge decision.
