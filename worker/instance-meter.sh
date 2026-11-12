#!/bin/bash
# instance-meter.sh — meter THIS instance's running time into its project's cost totals.
#
# Runs on EVERY boot of BOTH the researcher and the GPU workers, as its own transient systemd unit
# (`ar-instance-meter`, started by researcher boot / worker-bootstrap.sh via systemd-run), so it lives
# exactly as long as the instance is up — independent of who started the instance (Start button,
# run-experiment sub-agent, provisioning boot) and of whether the agent behaves.
#
# Every TICK seconds it reports the absolute interval [LAST, NOW] to agent_api `meter_instance`.
# The first interval starts at the EC2 LaunchTime of this run (EC2 resets it on every start), so boot
# time is billed too. agent_api charges only beyond the instance's high-water mark, so a retried,
# overlapping or duplicated report never double-charges; if a report fails, LAST is not advanced and
# the next tick re-sends the whole uncharged interval. On SIGTERM (stop/shutdown) a final report is
# sent, so at most one TICK is lost.
set -uo pipefail

TICK="${AR_METER_TICK_S:-60}"
FN="AutoResearch-agent_api"
OUT="$(mktemp /tmp/ar-meter.XXXXXX)"
export PATH="$PATH:/usr/local/bin:/usr/bin:/bin"

T=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
imds() { curl -s -H "X-aws-ec2-metadata-token: $T" "http://169.254.169.254/latest/meta-data/$1"; }
IID=$(imds instance-id)
export AWS_REGION="${AWS_REGION:-$(imds placement/region)}"
[[ "$IID" =~ ^i-[0-9a-f]+$ ]] || { echo "[meter] bad instance id '$IID'"; exit 1; }   # systemd restarts us

# Metering starts at LaunchTime regardless of how long these lookups take, so retry them (early
# boot: network/IAM credentials may not be ready yet) instead of giving up on the whole boot.
PID=""
for _ in $(seq 1 60); do
  PID=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$IID" --region "$AWS_REGION" \
        --query "Tags[?Key=='ProjectId'].Value | [0]" --output text 2>/dev/null)
  [[ "$PID" =~ ^[a-z0-9-]+$ ]] && break
  sleep 10
done
[[ "$PID" =~ ^[a-z0-9-]+$ ]] || { echo "[meter] could not resolve ProjectId tag ('$PID')"; exit 1; }

read_token() {  # legacy projects have no token (agent_api then allows the call)
  local t
  t=$(aws ssm get-parameter --name "/auto-research/projects/$PID/agent-token" --with-decryption \
      --region "$AWS_REGION" --query Parameter.Value --output text 2>/dev/null || true)
  # Values are interpolated into JSON below — refuse anything outside the token alphabet.
  if [[ "$t" =~ ^[A-Za-z0-9_-]+$ ]] && [ "$t" != "None" ]; then TOKEN="$t"; fi
}
TOKEN=""
read_token

LAUNCH=""
for _ in $(seq 1 30); do
  LAUNCH=$(aws ec2 describe-instances --instance-ids "$IID" --region "$AWS_REGION" \
           --query 'Reservations[0].Instances[0].LaunchTime' --output text 2>/dev/null) && [ -n "$LAUNCH" ] && break
  sleep 5
done
NOW=$(date +%s)
LAST=$(date -d "$LAUNCH" +%s 2>/dev/null || echo "")
if ! [[ "$LAST" =~ ^[0-9]+$ ]] || [ "$LAST" -gt "$NOW" ]; then
  # Fallback: kernel uptime (slightly under-counts the pre-kernel boot phase).
  LAST=$(( NOW - $(cut -d. -f1 /proc/uptime) ))
fi
echo "[meter] project=$PID instance=$IID metering from $(date -u -d "@$LAST" +%FT%TZ) every ${TICK}s"

FAILS=0
report() {  # report [LAST, $1]; advance LAST only on success
  local end="$1"
  [ "$end" -gt "$LAST" ] || return 0
  local tok=""
  [ -n "$TOKEN" ] && tok=",\"_token\":\"$TOKEN\""
  local payload="{\"action\":\"meter_instance\",\"project_id\":\"$PID\",\"instance_id\":\"$IID\",\"start_epoch\":$LAST,\"end_epoch\":$end$tok}"
  if aws lambda invoke --function-name "$FN" --region "$AWS_REGION" --cli-binary-format raw-in-base64-out \
       --payload "$payload" "$OUT" >/dev/null 2>&1 \
     && python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("ok") else 1)' "$OUT"; then
    LAST="$end"
    FAILS=0
  else
    FAILS=$((FAILS + 1))
    echo "[meter] report failed (will retry with the full interval): $(head -c 300 "$OUT" 2>/dev/null)"
    [ $((FAILS % 3)) = 0 ] && read_token   # e.g. the token could not be read at boot
  fi
}

STOP=0
trap 'STOP=1' TERM INT
while true; do
  sleep "$TICK" & wait $! 2>/dev/null
  report "$(date +%s)"
  if [ "$STOP" = 1 ]; then
    rm -f "$OUT"
    exit 0
  fi
done
