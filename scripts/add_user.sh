#!/usr/bin/env bash
set -euo pipefail

# Add a user to the Cognito pool with a permanent password.
# Usage: ./add_user.sh <username> <password>

USERNAME="${1:?Usage: ./add_user.sh <username> <password>}"
PASSWORD="${2:?Usage: ./add_user.sh <username> <password>}"
REGION="${AWS_REGION:-$(aws configure get region)}"

USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name AutoResearchAuthStack --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey==\`CognitoUserPoolId\`].OutputValue" --output text)

if [ -z "$USER_POOL_ID" ] || [ "$USER_POOL_ID" = "None" ]; then
  echo "Error: Could not retrieve User Pool ID. Is AutoResearchAuthStack deployed?"
  exit 1
fi

aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username "$USERNAME" \
  --message-action SUPPRESS \
  --region "$REGION"

aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username "$USERNAME" \
  --password "$PASSWORD" \
  --permanent \
  --region "$REGION"

echo "User '$USERNAME' created with permanent password. Ready to log in."
