#!/usr/bin/env bash
# Deploy the ARRL 10 GHz log analyzer to AWS.
#
#   ./deploy.sh
#
# Optional settings (environment variables):
#   DOMAIN_NAME=10ghz.microwavedx.com   custom host name (needs a Route 53 hosted zone)
#   ALT_DOMAIN_NAME=www.microwavedx.com second host name on the same certificate
#   HOSTED_ZONE_ID=Z0123...             skip the automatic hosted-zone lookup
#   CERT_ARN=arn:aws:acm:us-east-1:...  use an existing us-east-1 certificate
#   ALERT_EMAIL=you@example.com         app and account budget alerts
#   APP_BUDGET_USD=5                    monthly budget for this app alone
#   MONTHLY_BUDGET_USD=10               whole-account safety-net budget
#   RESERVED_CONCURRENCY=3              Lambda concurrency cap (auto-detected if unset)
#   KEEP_IMAGES=3                       Lambda image versions to keep in ECR
#   AWS_REGION=us-east-2  STACK_NAME=arrl-10ghz-web  AWS_PROFILE=...
#
# Needs: AWS CLI v2, AWS SAM CLI, and a running Docker (or Finch/Podman with a
# docker-compatible socket) to build the Lambda container image.
set -euo pipefail
cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-2}"
STACK_NAME="${STACK_NAME:-arrl-10ghz-web}"
CERT_STACK_NAME="${STACK_NAME}-cert"

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ checks
command -v aws >/dev/null    || fail "AWS CLI not found (https://aws.amazon.com/cli/)."
command -v sam >/dev/null    || fail "AWS SAM CLI not found (brew install aws-sam-cli, or pip install aws-sam-cli)."
command -v docker >/dev/null || fail "Docker not found -- it's needed to build the Lambda image."
docker info >/dev/null 2>&1  || fail "Docker is installed but not running. Start Docker Desktop and retry."
# SAM talks to Docker through DOCKER_HOST or /var/run/docker.sock only, but
# Docker Desktop (unless its "default Docker socket" option is on) listens on
# a per-user socket reached through the docker CLI's context. Point SAM at
# whatever endpoint the docker CLI is actually using.
if [[ -z "${DOCKER_HOST:-}" && ! -S /var/run/docker.sock ]]; then
  DOCKER_HOST=$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)
  [[ -n "$DOCKER_HOST" ]] && export DOCKER_HOST && echo "Using Docker at $DOCKER_HOST"
fi
ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || fail "AWS credentials not configured. Run 'aws configure' (or 'aws sso login')."
say "Deploying stack '$STACK_NAME' to account $ACCOUNT in $REGION"

# ------------------------------------------------------------------ custom domain
if [[ -n "${DOMAIN_NAME:-}" ]]; then
  if [[ -z "${HOSTED_ZONE_ID:-}" ]]; then
    say "Looking up the Route 53 hosted zone for $DOMAIN_NAME"
    candidate="$DOMAIN_NAME"
    while [[ "$candidate" == *.* ]]; do
      found=$(aws route53 list-hosted-zones-by-name --dns-name "$candidate" --max-items 1 \
        --query "HostedZones[?Name=='${candidate}.' && Config.PrivateZone==\`false\`].Id | [0]" --output text)
      if [[ -n "$found" && "$found" != "None" ]]; then HOSTED_ZONE_ID="${found##*/}"; break; fi
      candidate="${candidate#*.}"
    done
    [[ -n "${HOSTED_ZONE_ID:-}" ]] || fail "No public Route 53 hosted zone found for $DOMAIN_NAME. Set HOSTED_ZONE_ID."
    echo "    hosted zone: $HOSTED_ZONE_ID ($candidate)"
  fi

  if [[ -z "${CERT_ARN:-}" ]]; then
    say "Creating/updating the ACM certificate in us-east-1 (DNS validation can take a few minutes)"
    aws cloudformation deploy --region us-east-1 \
      --stack-name "$CERT_STACK_NAME" \
      --template-file certificate.yaml \
      --no-fail-on-empty-changeset \
      --tags app=arrl-10ghz-web \
      --parameter-overrides \
        "DomainName=$DOMAIN_NAME" \
        "AlternateDomainName=${ALT_DOMAIN_NAME:-}" \
        "HostedZoneId=$HOSTED_ZONE_ID"
    CERT_ARN=$(aws cloudformation describe-stacks --region us-east-1 --stack-name "$CERT_STACK_NAME" \
      --query "Stacks[0].Outputs[?OutputKey=='CertificateArn'].OutputValue" --output text)
    echo "    certificate: $CERT_ARN"
  fi
fi

# ------------------------------------------------------------------ concurrency cap
if [[ -z "${RESERVED_CONCURRENCY:-}" ]]; then
  limit=$(aws lambda get-account-settings --region "$REGION" \
    --query AccountLimit.ConcurrentExecutions --output text)
  if (( limit >= 100 )); then
    RESERVED_CONCURRENCY=3
  else
    RESERVED_CONCURRENCY=0
    echo "NOTE: this account's Lambda concurrency limit is $limit, too low to reserve any."
    echo "      The API rate limit still applies. Ask AWS Support for a quota increase"
    echo "      (Service Quotas > Lambda > Concurrent executions) to enable the cap."
  fi
fi

# ------------------------------------------------------------------ backend
say "Building the Lambda container image"
sam build

say "Deploying the CloudFormation stack"
params=(
  "ReservedConcurrency=$RESERVED_CONCURRENCY"
  "AppBudgetUsd=${APP_BUDGET_USD:-5}"
  "MonthlyBudgetUsd=${MONTHLY_BUDGET_USD:-10}"
)
[[ -n "${DOMAIN_NAME:-}" ]]     && params+=("DomainName=$DOMAIN_NAME" "CertificateArn=$CERT_ARN" "HostedZoneId=$HOSTED_ZONE_ID")
[[ -n "${ALT_DOMAIN_NAME:-}" ]] && params+=("AlternateDomainName=$ALT_DOMAIN_NAME")
[[ -n "${ALERT_EMAIL:-}" ]]     && params+=("AlertEmail=$ALERT_EMAIL")
sam deploy --stack-name "$STACK_NAME" --region "$REGION" --parameter-overrides "${params[@]}"

output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
BUCKET=$(output WebsiteBucketName)
DISTRIBUTION=$(output DistributionId)
URL=$(output WebsiteURL)

# ------------------------------------------------------------------ frontend
say "Uploading the website to s3://$BUCKET"
# stats/data.json is written by the daily stats job, not deployed -- never delete it.
aws s3 sync frontend/ "s3://$BUCKET/" --delete --exclude "*.html" --exclude "stats/data.json" \
  --cache-control "public, max-age=300"
aws s3 sync frontend/ "s3://$BUCKET/" --delete --exclude "*" --include "*.html" \
  --cache-control "no-cache" --content-type "text/html; charset=utf-8"

say "Invalidating the CloudFront cache"
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/*" \
  --query Invalidation.Id --output text

# ------------------------------------------------------------------ cost housekeeping
# SAM creates the image repository in its own companion stack, so it isn't in
# template.yaml. Every deploy pushes a new ~0.4 GB image; keep only the newest
# few (the one Lambda runs is always the newest), and tag the repository so
# the app budget counts it. Non-fatal: the site is already deployed by now.
tidy_images() {
  local image_uri repo repo_arn keep="${KEEP_IMAGES:-3}"
  image_uri=$(aws lambda get-function --region "$REGION" \
    --function-name "$(output ProcessFunctionName)" --query Code.ImageUri --output text) || return 1
  repo=${image_uri#*/}; repo=${repo%%@*}; repo=${repo%%:*}
  repo_arn=$(aws ecr describe-repositories --region "$REGION" --repository-names "$repo" \
    --query 'repositories[0].repositoryArn' --output text) || return 1
  aws ecr put-lifecycle-policy --region "$REGION" --repository-name "$repo" \
    --lifecycle-policy-text "{\"rules\":[{\"rulePriority\":1,\"description\":\"Keep the newest $keep images\",\"selection\":{\"tagStatus\":\"any\",\"countType\":\"imageCountMoreThan\",\"countNumber\":$keep},\"action\":{\"type\":\"expire\"}}]}" \
    >/dev/null || return 1
  aws ecr tag-resource --region "$REGION" --resource-arn "$repo_arn" \
    --tags Key=app,Value=arrl-10ghz-web || return 1
  echo "    $repo: keeping the newest $keep images"
}
say "Tidying the Lambda image repository"
tidy_images || echo "WARNING: couldn't set the image cleanup rule; the site is deployed, rerun ./deploy.sh later."

# The app budget filters on the app=arrl-10ghz-web tag, which only works once
# "app" is an active cost allocation tag. AWS can take up to 24 hours after the
# first deploy to discover a new tag, so this may need a later rerun.
if aws ce update-cost-allocation-tags-status --region us-east-1 \
     --cost-allocation-tags-status TagKey=app,Status=Active >/dev/null 2>&1; then
  echo "    'app' cost allocation tag is active (the app budget can see this app's costs)"
else
  echo "NOTE: couldn't activate the 'app' cost allocation tag yet -- AWS may not have"
  echo "      discovered it. Rerun ./deploy.sh tomorrow, or activate it under"
  echo "      Billing > Cost allocation tags. Until then the app budget reads \$0."
fi

say "Refreshing the stats dashboard"
if aws lambda invoke --region "$REGION" --function-name "$(output ProcessFunctionName)" \
     --cli-binary-format raw-in-base64-out --payload '{"action": "aggregate"}' \
     "${TMPDIR:-/tmp}/arrl-stats.json" >/dev/null 2>&1; then
  echo "    $URL/stats/ updated ($(cat "${TMPDIR:-/tmp}/arrl-stats.json"))"
else
  echo "WARNING: couldn't refresh the stats now; the daily job will at 06:15 UTC."
fi

say "Done!  $URL"
echo "    (a brand-new CloudFront distribution can take ~5-10 minutes to answer everywhere)"
echo "    Run ./verify-deployment.sh to smoke-test it."
