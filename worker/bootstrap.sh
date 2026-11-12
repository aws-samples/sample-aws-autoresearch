#!/bin/bash
# worker/bootstrap.sh — GPU worker readiness.
#
# Runs on EVERY boot (installed as a systemd oneshot by the instance UserData launcher),
# and is idempotent via a marker file. The worker runs NO Claude agent: the run-experiment
# sub-agent drives it over SSH. This script only makes the box ready (data + deps) and
# arms a shutdown watchdog as a runaway-cost backstop.
#
# Expected: ProjectId + Role=worker instance tags; the code repo contains setup.sh
# (uv sync + uv run prepare.py) which is run once to stage the dataset + tokenizer on EBS.

set -euo pipefail

U="ubuntu"
HOME_DIR="/home/$U"
WORK_DIR="$HOME_DIR/workspace"
MARKER="$HOME_DIR/.worker-bootstrap-complete"
WATCHDOG_MIN="${WORKER_WATCHDOG_MIN:-90}"

IMDS_TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
imds() { curl -s -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" "http://169.254.169.254/latest/meta-data/$1"; }
INSTANCE_ID=$(imds instance-id)
AWS_REGION="${AWS_REGION:-$(imds placement/region)}"

tags=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$INSTANCE_ID" --region "$AWS_REGION" --output json)
PROJECT_ID=$(echo "$tags" | jq -r '.Tags[] | select(.Key=="ProjectId") | .Value')
CODE_REPO="${PROJECT_ID}-code"
LOG_GROUP="/auto-research/${PROJECT_ID}"

aws logs create-log-stream --log-group-name "$LOG_GROUP" --log-stream-name "worker-$INSTANCE_ID" --region "$AWS_REGION" 2>/dev/null || true
log() {
    echo "$1"
    local ts; ts=$(date +%s%3N)
    local msg; msg=$(printf '%s' "$1" | jq -Rs .)
    aws logs put-log-events --log-group-name "$LOG_GROUP" --log-stream-name "worker-$INSTANCE_ID" \
        --log-events "[{\"timestamp\":$ts,\"message\":$msg}]" --region "$AWS_REGION" >/dev/null 2>&1 || true
}

# --- Cost metering: this worker's running time → the project's worker_cost_usd (every boot). ---
# A separate transient unit (not a child of this script): when this boot script exits, systemd
# tears down the boot unit's cgroup, which would kill a plain background process.
systemd-run --unit=ar-instance-meter --collect -p After=network-online.target -p Wants=network-online.target \
    -p Restart=on-failure -p RestartSec=30 \
    --setenv=AWS_REGION="$AWS_REGION" /bin/bash "$HOME_DIR/.auto-research/instance-meter.sh" >/dev/null 2>&1 \
    || log "WARNING: could not start the instance cost meter (already running?)"

# --- Runaway-cost backstop: self-stop after N minutes even if the sub-agent dies. ---
# N must exceed the project's hard per-experiment timeout (the dispatcher kills the sub-agent and
# stops the worker at that point), so a legitimate long experiment is never cut short by it.
EXP_TIMEOUT_MIN=$(
  TOK=$(aws ssm get-parameter --name "/auto-research/projects/$PROJECT_ID/agent-token" --with-decryption \
        --region "$AWS_REGION" --query Parameter.Value --output text 2>/dev/null || true)
  OUTF=$(mktemp)
  python3 -c 'import json,sys; print(json.dumps({"action":"get_project","project_id":sys.argv[1],"_token":sys.argv[2]}))' \
      "$PROJECT_ID" "$TOK" > "$OUTF.req" 2>/dev/null || true
  aws lambda invoke --function-name AutoResearch-agent_api --region "$AWS_REGION" --cli-binary-format raw-in-base64-out \
      --payload "file://$OUTF.req" "$OUTF" >/dev/null 2>&1 || true
  python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["project"].get("experiment_timeout_minutes") or 0))' \
      "$OUTF" 2>/dev/null || echo 0
  rm -f "$OUTF" "$OUTF.req"
)
[[ "$EXP_TIMEOUT_MIN" =~ ^[0-9]+$ ]] || EXP_TIMEOUT_MIN=0
[ $((EXP_TIMEOUT_MIN + 30)) -gt "$WATCHDOG_MIN" ] && WATCHDOG_MIN=$((EXP_TIMEOUT_MIN + 30))
# The first (provisioning) boot runs setup.sh — dataset/dependency staging can take much longer.
if [ ! -f "$MARKER" ] && [ "$WATCHDOG_MIN" -lt 240 ]; then WATCHDOG_MIN=240; fi
log "Watchdog: this worker stops itself after ${WATCHDOG_MIN} min (experiment timeout ${EXP_TIMEOUT_MIN} min)."
# InstanceInitiatedShutdownBehavior=stop makes this a STOP (not terminate), preserving EBS.
# A systemd timer unit, NOT a background `sleep` — a background child of this script would be killed
# with the boot unit's cgroup as soon as this script exits, silently disarming the backstop.
if ! systemd-run --unit=ar-worker-watchdog --collect --on-active="${WATCHDOG_MIN}m" /sbin/shutdown -h now >/dev/null 2>&1; then
    nohup bash -c "sleep $((WATCHDOG_MIN * 60)); shutdown -h now" >/dev/null 2>&1 &
fi

if [ ! -f "$MARKER" ]; then
    log "=== Worker first-time bootstrap (instance $INSTANCE_ID) ==="
    sudo -u "$U" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null 2>&1 || true
    pip3 install --quiet git-remote-codecommit 2>/dev/null || true

    # Git identity — the sub-agent commits fixes on the worker during the debug loop.
    sudo -u "$U" git config --global user.email "worker@auto-research" || true
    sudo -u "$U" git config --global user.name "auto-research worker" || true

    sudo -u "$U" mkdir -p "$WORK_DIR"
    if [ ! -d "$WORK_DIR/code/.git" ]; then
        sudo -u "$U" git clone "codecommit::${AWS_REGION}://${CODE_REPO}" "$WORK_DIR/code"
    fi

    if [ -f "$WORK_DIR/code/setup.sh" ]; then
        log "Running setup.sh (uv sync + prepare.py — stages dataset on EBS)..."
        sudo -u "$U" bash -lc "cd $WORK_DIR/code && bash setup.sh" 2>&1 | tail -5 | while read -r l; do log "$l"; done || log "setup.sh returned non-zero"
    fi

    touch "$MARKER"
    log "=== Worker ready (first-boot setup complete) ==="

    # This is the PROVISIONING boot: create_project launched the worker only to stage the dataset +
    # deps onto its EBS volume once. That volume survives stop/start, so now power OFF — after
    # auto_start only the researcher should stay up; each worker is woken on demand by the
    # run-experiment sub-agent (worker-start.sh), and on that later boot the marker already exists so
    # we take the 'waking' path below and stay up for the sub-agent.
    # InstanceInitiatedShutdownBehavior=stop makes this a STOP (not terminate), preserving EBS.
    log "First-boot setup done — stopping worker until an experiment needs it (researcher stays up)."
    shutdown -h now
    exit 0
else
    log "=== Worker waking (already bootstrapped) ==="
    sudo -u "$U" git -C "$WORK_DIR/code" fetch --all 2>/dev/null || true
fi

log "Worker idle and SSH-ready; awaiting run-experiment sub-agent."
