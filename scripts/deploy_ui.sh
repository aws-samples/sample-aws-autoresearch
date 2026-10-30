#!/usr/bin/env bash
set -euo pipefail

# Build and deploy the React UI to S3/CloudFront.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UI_DIR="$SCRIPT_DIR/../user-interface"
REGION="${AWS_REGION:-$(aws configure get region)}"

get_output() {
  aws cloudformation describe-stacks --stack-name "$1" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey==\`$2\`].OutputValue" --output text
}

echo "Fetching stack outputs..."
UI_BUCKET=$(get_output AutoResearchWebAppStack UIBucketName)
DIST_ID=$(get_output AutoResearchWebAppStack DistributionId)
DIST_URL=$(get_output AutoResearchWebAppStack DistributionUrl)

cat > "$UI_DIR/.env.local" <<EOF
VITE_COGNITO_USER_POOL_ID=$(get_output AutoResearchAuthStack CognitoUserPoolId)
VITE_COGNITO_CLIENT_ID=$(get_output AutoResearchAuthStack CognitoClientId)
VITE_COGNITO_IDENTITY_POOL_ID=$(get_output AutoResearchAuthStack CognitoIdentityPoolId)
VITE_API_URL=$(get_output AutoResearchApiStack ApiUrl)
VITE_AWS_REGION=$REGION
EOF
echo "Generated .env.local"

cd "$UI_DIR"

echo "Installing dependencies..."
npm install --silent

echo "Building..."
npm run build

echo "Syncing to s3://$UI_BUCKET ..."
aws s3 sync dist/ "s3://$UI_BUCKET" --delete --region "$REGION"

echo "Invalidating CloudFront distribution $DIST_ID ..."
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" --no-cli-pager

echo "Done. UI deployed at $DIST_URL"
