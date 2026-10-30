---
name: research-step
description: One full iteration of the autonomous research loop — sync context, acquire knowledge, design one experiment per available worker, run them all concurrently (each worker Start→Run→Stop), then analyze, keep the best, reflect, and report.
---

# Research Step

You are an **AI Researcher** using the scientific method end-to-end on this project.
This skill is one complete iteration of your loop.

## Environment

 * **Working Directory**: you are in `~/workspace/code`.
 * `$STEP_ID`: this environmental variable identifies this whole iteration (one per loop). Within the step you run several experiments; give each a short kebab-case **title** and build its id as `${STEP_ID}-<title>` (e.g. `${STEP_ID}-baseline`, `${STEP_ID}-cosine-warmup`). The title makes every experiment id, branch, and log stream self-explanatory.

**Commands** — Every mechanical operation is a subcommand of the `aws-research step` CLI (installed on
this host). Call them as:

```bash
aws-research step <command> [args...]
```

Each prints what it did, validates its inputs, and is safe to re-run; pass `--help` to see usage.

| command | what it does |
|---|---|
| `aws-research step project-info [field]` | print the project record, or one field (`s3_data`, `best_metric`, …) |
| `aws-research step list-experiments [state]` | print list of the past experiments |
| `aws-research step pull-context` | orient: workers/N, best, spend/budget, recent experiments |
| `aws-research step guidance {list\|done <id>}` | read / clear the human guidance inbox |
| `aws-research step update-project best_metric <value>` | record the new best metric (`status` & `spent_usd` are loop-managed — don't set them; progress notes go in `report/status.md`) |
| `aws-research step experiment-branch <EXP_ID>` | branch experiment/`EXP_ID` from the current best `main` |
| `aws-research step experiment-push <EXP_ID> "<hyp>"` | commit + push that branch, return to `main` |
| `aws-research step create-experiment <EXP_ID> <WORKER_ID> "<hyp>" "<change>"` | create the ledger record (state `running`) |
| `aws-research step dispatch-experiments <EXP_ID> <WORKER_ID> …` | run all experiments concurrently; prints result array |
| `aws-research step finalize-experiment <state> '<result_json>'` | write an experiment's terminal record (cost read from the result; auto-prunes the checkpoint if `discarded`/`crashed`) |
| `aws-research step keep-best <EXP_ID> <best_metric> "<msg>"` | merge the winner into `main`, record best |

## Cost model — GPUs are OFF unless an experiment is running

**Run modes.** `aws-research step pull-context` prints `EXPERIMENT_RUNNER` and `N_WORKERS`/`WORKERS`. In **ec2** mode
(default) worker GPUs are stopped by default; a worker is powered on only for its experiment run and
stopped immediately after (Start → Run → Stop), by the `run-ec2-experiment` sub-agent. In **local**
mode there are no workers — experiments run on this (GPU) researcher instance itself via the
`run-local-experiment` sub-agent, and `aws-research step pull-context` reports a single slot (`WORKERS=local`,
`N_WORKERS=1`). Either way you drive experiments the same: design N (= `N_WORKERS`), then dispatch.
Steps 1–5 (sync, guidance, knowledge acquisition, design, implement) are just reasoning/editing.

## Durable artifacts in S3

The project's S3 folder — `$S3`, the `s3_data` from `aws-research step project-info s3_data` — is the durable home
for anything worth keeping across experiments and worker restarts.
Persist important data there (workers are ephemeral). Maintain this layout:
- `$S3/data/`        — (optional) an explicit pre-processed dataset ARCHIVE for cross-worker reuse; the per-worker package/data cache is NOT copied here — it already persists on the worker's EBS across stop/start
- `$S3/checkpoints/` — the most promising model checkpoints (keep the best; prune the rest)
- `$S3/benchmarks/`  — per-experiment benchmark / evaluation outputs, for error analysis
- `$S3/report/`, `$S3/logs/`, `$S3/experiments/` — charts, researcher log, per-experiment run logs (already written)

The experiment sub-agent uploads checkpoints / benchmarks to S3 during the run (in ec2 mode the
worker is the only place they live, and it is about to be stopped). Checkpoints stay bounded automatically:
`aws-research step finalize-experiment` prunes a `discarded`/`crashed` experiment's checkpoint as it records the
outcome (step 7), so only the winner's checkpoint survives.

## Concurrency — one distinct experiment per available slot

Each iteration you run **N experiments in parallel, where N = `N_WORKERS`** (the number of available
worker GPUs in ec2 mode, or **1** in local mode), and each is a **different hypothesis**. Every
experiment gets its own branch and ledger record. In ec2 mode each runs on its own worker (all
concurrent, each stopped as its run finishes); in local mode the single slot runs one experiment at a
time on the researcher's own GPU. The loop is sequential, so all slots are free again next step.

Follow these following steps in order.

## 1. Sync Context
First run the following script to print `N_WORKERS`, `WORKERS`, `BEST_METRIC`, `SPENT_USD`/`BUDGET_USD`, and the recent experiments: 
```bash
aws-research step pull-context
```

Then read:
- **Project Specification** (`~/workspace/code/auto-research.json`): Contains the starting project specification.
- **LLM Wiki** (`~/workspace/wiki`): Contains all the knowledge acquired about this research project. Read its index (`~/workspace/wiki/index.md`) to get a quick overview about its content.
- **Status Report** (`~/workspace/code/report/status.md`): Contains the actual state of the project, including all the learnings from the past experiments.

## 2. Human Directives
Run the following script to receive messages from the human inbox:
```bash
aws-research step guidance list
```

Let unprocessed guidance steer this iteration. After acting on each item, mark it processed:
```bash
aws-research step guidance done "$GUIDANCE_ID"
```

## 3. Knowledge Acquisition
Use the `knowledge-acquisition` skill to grow the LLM Wiki about the current research topic.
Starting from a thorough literature review that could be used for a research paper on this topic.
For each task and sub-task identify the SOTA solutions, and find references about their implementations.
You want to stand on the shoulders of giants: think about all the possible solutions and experiments you could run, and search existing literature about each of these approaches.
Focus on **actionable takeaways** for the next experiments (a concrete change to try, with its mechanism).

1. Identify the specific research questions.
2. Gather sources (web search via the `web-search` skill — the built-in WebSearch/WebFetch tools do not
   work on Bedrock — plus arXiv, Semantic Scholar, OpenAlex, Papers With Code, GitHub) into
   `~/workspace/wiki/raw/`; See also the seed references in the project brief (`README.md`, mirrored
   into `wiki/concepts/research-context.md`).
3. Distill into small, current `concepts/…md` pages tagged `#literature`/`#technique`, cross-linked
   with `[[wikilinks]]`.
4. Update `index.md` with a short description of each new page, and append a changelog entry to the `log.md`.
5. Push these changes to the remote repository to allow your fellow researchers to access this knowledge.

## 4. Hypotheses
Maintain a list of promising Hypotheses in the status report (`~/workspace/code/report/status.md`).
Re-order their priority in light of the new knowledge you acquired from the wiki and from the experiments.
Each hypothesis should be implementable and testable applying suitable code changes.

Select the **N most promising hypotheses**: one experiment per worker.

If this is the **First iteration**, the first experiment is the **baseline** (title `baseline`): change nothing. Fore example, if available, run `src/train.py` as-is.

## 5. Implement each experiment on its own branch
For each experiment, choose a short kebab-case **title** describing the idea (`baseline`, `cosine-warmup`, `bigger-batch`, …), and use it to generate the Experiment ID `E_ID=${STEP_ID}-<title>`.

Branch from the current best, implement the code changes, then commit and push:
```bash
aws-research step experiment-branch "$E_ID"     # checkout main, pull, create experiment/$E
# ... apply this experiment's edits to src/train.py with the Edit tool ...
aws-research step experiment-push "$E_ID" "<one-line hypothesis>"
```

## 6. Execute all N experiments concurrently (each worker Start→Run→Stop)
First create one DB entry for each experiment:
```bash
# one call per experiment (WORKER_ID = the i-th of WORKERS from step 1):
aws-research step create-experiment "$E_ID" "<WORKER_ID>" "<experiment hypothesis>" "<experiment description>"
```

Then dispatch all the experiments at once. `aws-research step dispatch-experiments` runs a nested process per experiment, so each has its own observable log stream, and it prints a JSON array of the N result objects:
```bash
RESULTS=$(aws-research step dispatch-experiments "$E_ID0" "<WORKER_0>" "$E_ID1" "<WORKER_1>" ...)
echo "$RESULTS"
```

Each result object has: `experiment_id`, `metric_value`, `peak_vram_mb`, `status` (the sub-agent's
verdict), `run_seconds`, `worker_uptime_seconds`, `cost_usd` (GPU cost, computed from uptime),
`run_log_s3_uri`, `log_stream`, `agent_log_s3_uri`, `notes`.

## 7. Analyze — compare all N, keep the single best
Read `metric_direction` and the current `best_metric`. Classify each of the N results as `kept`/`discarded`/`crashed` (crash = no metric; discard = ran but didn't beat best, or a tiny gain for a lot of ugly complexity; candidate = strictly better than best).
Among candidates, **keep only the single best** — merge exactly that branch:
```bash
aws-research step keep-best "<winning-E_ID>" "<new best>" "<before> -> <after>"
```

Finalize **every** experiment DB-entry recording its terminal state. This also auto-prunes the
checkpoint of any `discarded`/`crashed` experiment, so only the winner's checkpoint is kept — no
separate cleanup step:
```bash
# for each result object in $RESULTS (e.g. select it with: echo "$RESULTS" | jq -c '.[N]'):
aws-research step finalize-experiment "kept|discarded|crashed" '<result_json>'
```

## 8. Update the Knowledge
Persist all the knowledge you acquired from the experiments within the LLM Wiki: `~/workspace/wiki`.
Add/update the relevant `concepts/…md` page tagged `#experiment`/`#lesson`, cross-link with `[[wikilinks]]`, update `index.md`, append to `log.md`.

## 9. Update the Report
Update the `report/status.md`, keeping in mind 3 different goals:
 1. Keep track of the research status, providing:
   * description of the curent best solution
   * possible observations coming from the error-analysis of the current best solution.
   * general lessons learned about what helps (positive results), and what doesn't (negative results)
   * a prioritised list of hypotheses to be tested experimentally
 2. Provide updates to the human supervisors of this research project: charts showing the progress of the tracked metric are very important.
 3. Possible basis for a scentific publication. Add:
   * an introduction about the problem
   * a brief overview of related work organized thematically

You can generate PNG **Charts** with matplotlib under the `/tmp` directory, and then upload them into the `<S3-URI>/report/` S3 folder. You can see the S3-URI folder running: `aws-research step project-info s3_data`. You can reference these images with **relative** paths (i.e. `./report/<name>.png`) — the web app rewrites those to presigned URLs.

You can link to wiki pages using  `[[wikilinks]]`.

If you are missing any metric, you can retrieve them running: `aws-research step list-experiments`.

When all steps are done, end your turn.
