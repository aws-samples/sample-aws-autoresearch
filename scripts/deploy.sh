#!/usr/bin/env bash
set -euo pipefail

# Deploy all CDK stacks and store the API URL in SSM for instances to read.
# Idempotent: safe to re-run. Pins the region to us-east-1 by default (override with AWS_REGION),
# manages a local Python venv, and bootstraps the environment if needed.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA_DIR="$SCRIPT_DIR/../infrastructure"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION" CDK_DEFAULT_ACCOUNT="$ACCOUNT"

echo "=== Deploying Auto-Research ==="
echo "Account: $ACCOUNT   Region: $REGION"
echo ""

echo "--- Bundling researcher skills + bootstrap into the create_project Lambda ---"
bash "$SCRIPT_DIR/package_lambdas.sh"

cd "$INFRA_DIR"

echo "--- Python venv + CDK dependencies ---"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

echo "--- Bootstrapping CDK (idempotent) ---"
cdk bootstrap "aws://$ACCOUNT/$REGION"

echo "--- Deploying all stacks ---"
# Restrict API CORS to the web app's CloudFront origin (security-review #5). On the first deploy
# the WebAppStack doesn't yet exist, so allowed_origins is unset and the API stack falls back to
# "*"; every subsequent deploy uses the real distribution URL. Ordering the WebApp stack first
# makes the follow-up ApiStack deploy pick it up automatically.
ALLOWED_ORIGINS=$(aws cloudformation describe-stacks \
  --stack-name AutoResearchWebAppStack --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionUrl'].OutputValue" --output text 2>/dev/null || echo "")
CDK_CONTEXT_ARGS=()
if [ -n "$ALLOWED_ORIGINS" ] && [ "$ALLOWED_ORIGINS" != "None" ]; then
  CDK_CONTEXT_ARGS+=(-c "allowed_origins=$ALLOWED_ORIGINS")
fi
cdk deploy --all --require-approval never "${CDK_CONTEXT_ARGS[@]}"

echo ""
echo "--- Storing API URL in SSM ---"
API_URL=$(aws cloudformation describe-stacks \
  --stack-name AutoResearchApiStack --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey==\`ApiUrl\`].OutputValue" --output text)

aws ssm put-parameter \
  --name "/auto-research/api-url" \
  --value "$API_URL" \
  --type String \
  --overwrite \
  --region "$REGION"

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "API URL: $API_URL"
echo "Dashboard: $(aws cloudformation describe-stacks \
  --stack-name AutoResearchWebAppStack --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey==\`DistributionUrl\`].OutputValue" --output text)"
echo ""
WORKER_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name AutoResearchComputeStack --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey==\`WorkerKeyPairId\`].OutputValue" --output text 2>/dev/null || echo "")
echo "Worker SSH private key (SSM SecureString): /ec2/keypair/${WORKER_KEY_ID}"
echo ""
echo "Next steps:"
echo "  1. Create a user:  ./scripts/add_user.sh <username> <password>"
echo "  2. Deploy the UI:  ./scripts/deploy_ui.sh"
echo "  3. Create a project via the dashboard, then press Start"
