# Deploying to AWS

This guide puts the analyzer on AWS: CloudFront in front of a private S3 site, plus an API
Gateway endpoint and a container-image Lambda. It optionally adds a custom domain such as
microwavedx.com.

I recommend two passes. First deploy on the default CloudFront URL to prove the app works,
then add the custom domain. If something breaks, you'll know whether it's the app or the
domain setup.

If you haven't yet, get the app running locally first: [LOCAL_SETUP.md](LOCAL_SETUP.md).

## Before you start

You'll need:

- **An AWS account** you can sign in to, with a user or SSO role that has
  **AdministratorAccess**. The deploy creates CloudFormation stacks, S3 buckets, Lambda, an
  ECR image repository, API Gateway, CloudFront, IAM roles, Route 53 records, an ACM
  certificate and a budget. Admin is the simplest way to cover all of that.
- **About 30–45 minutes** the first time. Most of it is waiting on the image build and
  CloudFront.
- **A clone of this repo** (Step 1 of [LOCAL_SETUP.md](LOCAL_SETUP.md)). The deploy
  doesn't need the Python venv.

## Step 1: Install the tools (one time)

On a Mac with Homebrew:

```bash
brew install awscli aws-sam-cli
```

On an Apple Silicon Mac, `which brew` must say `/opt/homebrew/bin/brew`. If `brew` fails
with `Bad CPU type in executable`, you have an old Intel Homebrew in `/usr/local` (usually
carried over by Migration Assistant). Install the Apple Silicon version alongside it:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Run the "Next steps" commands the installer prints (they add `/opt/homebrew` to your PATH),
open a new terminal, and confirm `which brew` shows `/opt/homebrew/bin/brew`.

If you'd rather skip Homebrew, AWS publishes regular macOS `.pkg` installers for both the
[AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) and
the [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).

On Apple Silicon, also install Rosetta once. Docker uses it to build the Intel (x86_64)
Lambda image:

```bash
softwareupdate --install-rosetta --agree-to-license
```

Then install **Docker Desktop** from docker.com and start it. On Apple Silicon Macs, open
Docker Desktop → Settings → General and turn on **"Use Rosetta for x86_64/amd64
emulation"**. The Lambda image is built for Intel (x86_64), and Rosetta makes that build
much faster.

Check that all three work:

```bash
aws --version          # should say aws-cli/2.x
sam --version          # SAM CLI, version 1.x
docker info | head -3  # must print server info, not "Cannot connect"
```

## Step 2: Connect the AWS CLI to your account

If you use access keys:

```bash
aws configure
# AWS Access Key ID:     <your key>
# AWS Secret Access Key: <your secret>
# Default region name:   us-east-2
# Default output format: json
```

If you use IAM Identity Center (SSO), run `aws configure sso` once, then `aws sso login`
before each session.

Confirm it works:

```bash
aws sts get-caller-identity
```

You should see your account number. If you use a named profile, run
`export AWS_PROFILE=<name>` in this terminal before continuing.

## Step 3: Pre-flight checks (read-only; nothing is created)

**Lambda concurrency limit:**

```bash
aws lambda get-account-settings --region us-east-2 --query AccountLimit.ConcurrentExecutions
```

- **1000:** `deploy.sh` will cap the app at 10 simultaneous runs.
- **10:** your account is on the new-account limit. The deploy still works, but without the
  concurrency cap; the API rate limit still protects you. To enable the cap, request an
  increase to 1000 in the AWS console under Service Quotas → AWS Lambda → Concurrent
  executions. It's free and usually approved within a day.

**Your hosted zone** (only if you'll use a custom domain):

```bash
aws route53 list-hosted-zones-by-name --dns-name microwavedx.com --max-items 1
```

You should see a zone named `microwavedx.com.`. Also compare the registered domain's name
servers with the zone's. They must match or certificate validation will hang:

```bash
aws route53domains get-domain-detail --region us-east-1 --domain-name microwavedx.com --query 'Nameservers[].Name'
aws route53 get-hosted-zone --id <zone-id-from-above> --query 'DelegationSet.NameServers'
```

The two lists should contain the same four servers. If they don't, which happens when a
hosted zone was deleted and recreated, update the domain's name servers in Route 53 →
Registered domains to match the hosted zone, and wait for that change to take effect
before continuing.

**The old CDK stack** (from the earlier version of this project):

```bash
aws cloudformation describe-stacks --region us-east-2 --stack-name Arrl10GhzWebStack --query 'Stacks[0].StackStatus'
```

If it exists, leave it alone for now. [Step 7](#step-7-remove-the-old-cdk-stack) removes it
once the new one works.

## Step 4: Pass 1, deploy with the default CloudFront URL

Open a terminal in the project folder, check that Docker Desktop is running, then:

```bash
cd ~/Projects/10ghz-web-logger-analyzer
git pull
ALERT_EMAIL=you@example.com ./deploy.sh
```

Use your real address for `ALERT_EMAIL`. It sets up an AWS Budgets email if the whole
account's spend goes above 80% of $10 in a month, or is forecast to exceed $10. Change the
threshold with `MONTHLY_BUDGET_USD=20`.

What you'll see, in order:

1. **Account check:** `Deploying stack 'arrl-10ghz-web' to account 123456789012 in us-east-2`.
2. **`sam build`:** Docker builds the Lambda image. The first build downloads the AWS
   Python 3.12 base image and installs pandas, numpy and matplotlib. Expect 3–10 minutes;
   later builds are faster because of caching.
3. **`sam deploy`:** it creates an ECR repository, pushes the image (about 400 MB, so give
   it a few minutes on home upload speeds), shows the change set, and creates the stack.
   **The CloudFront distribution takes 5–15 minutes**, so a pause here is normal.
4. **Upload:** the site files go to S3, and the CloudFront cache is cleared.
5. **The URL:** `Done!  https://d1234abcd.cloudfront.net`.

## Step 5: Verify pass 1

```bash
./verify-deployment.sh
```

It checks three things and prints OK or FAIL for each:

- `OK   site loads (HTTP 200)`
- `OK   API generated N files ...`: the sample log went through the real Lambda.
- `OK   download works`: a presigned S3 link works.

If the site check fails right after the deploy, wait 5 minutes and run it again;
brand-new distributions take a while to reach every edge location.

Then test in a browser:

1. Open the `cloudfront.net` URL.
2. Run the sample CSV log, then one of your real logs.
3. **Test Google Sheets.** `verify-deployment.sh` doesn't cover it. Share a sheet as
   "Anyone with the link can view", paste the link, and analyze.

## Step 6: Pass 2, add a custom domain

First choose the host name. A subdomain like `10ghz.microwavedx.com` keeps the bare domain
free for other uses. If this app *is* the site, use the bare domain plus www.

Save your settings in a small file so every future deploy uses the same values. `.env.*`
files are git-ignored, so this never gets committed:

```bash
cat > .env.deploy <<'EOF'
export DOMAIN_NAME=10ghz.microwavedx.com
export ALERT_EMAIL=you@example.com
EOF
```

For the bare domain instead, use:

```bash
export DOMAIN_NAME=microwavedx.com
export ALT_DOMAIN_NAME=www.microwavedx.com
```

Then deploy:

```bash
source .env.deploy && ./deploy.sh
```

This time it also:

1. **Finds your hosted zone:** `hosted zone: Z0ABC... (microwavedx.com)`.
2. **Creates the certificate** in us-east-1 as the stack `arrl-10ghz-web-cert` (CloudFront
   only accepts certificates from us-east-1). CloudFormation adds the DNS validation record
   to your zone and **waits until ACM issues the certificate, usually 2–10 minutes**. The
   terminal looks idle during this; that's normal.
3. **Updates the main stack:** it attaches the certificate and your host name to
   CloudFront and creates the Route 53 A and AAAA records. The CloudFront update takes
   another 5–10 minutes.

Verify it:

```bash
./verify-deployment.sh https://10ghz.microwavedx.com
```

Also open the URL in a browser. The padlock should show a valid certificate for your
domain. If your browser can't find the name at first, DNS may still be propagating; it's
usually a few minutes.

If you'd rather create the certificate and DNS records by hand, the README has CLI and
console instructions under
[Doing the certificate and DNS steps by hand](../README.md#deploy-on-microwavedxcom).

## Step 7: Remove the old CDK stack

Only needed if Step 3 found `Arrl10GhzWebStack`. Its buckets must be empty before the stack
will delete:

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
aws s3 rm s3://arrl-10ghz-web-$ACCT-us-east-2 --recursive
aws s3 rm s3://arrl-10ghz-files-$ACCT-us-east-2 --recursive
aws cloudformation delete-stack --region us-east-2 --stack-name Arrl10GhzWebStack
aws cloudformation wait stack-delete-complete --region us-east-2 --stack-name Arrl10GhzWebStack
```

If the wait reports a failure, open CloudFormation → Arrl10GhzWebStack → Events in the
console and look for the first red row.

## Day-to-day

**Deploying changes** (new code, or after syncing a new CLI release):

```bash
cd ~/Projects/10ghz-web-logger-analyzer
git pull
source .env.deploy && ./deploy.sh
```

Always `source .env.deploy` first. SAM usually keeps previous settings when a variable is
missing, but this makes every deploy explicit.

**Picking up a new 10ghz-log-analyzer release:**

```bash
source .venv/bin/activate
scripts/sync-upstream.sh
pytest
git add -A && git commit -m "Sync upstream vX.Y.Z"
source .env.deploy && ./deploy.sh
```

**Watching the Lambda logs** (useful when someone reports a problem):

```bash
aws logs describe-log-groups --region us-east-2 --log-group-name-prefix arrl-10ghz-web \
  --query 'logGroups[].logGroupName' --output text
aws logs tail <that-name> --region us-east-2 --follow
```

**Costs:** check Billing → Cost Explorer after a week. For ham-radio traffic, expect cents
a month: ECR storage for the image is about $0.05, and Lambda, S3, API Gateway and
CloudFront mostly stay within the free tier.

## Taking it all down

```bash
REGION=us-east-2
out() { aws cloudformation describe-stacks --region $REGION --stack-name arrl-10ghz-web \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
aws s3 rm s3://$(out WebsiteBucketName) --recursive
aws s3 rm s3://$(out ResultsBucketName) --recursive
sam delete --stack-name arrl-10ghz-web --region $REGION
aws cloudformation delete-stack --region us-east-1 --stack-name arrl-10ghz-web-cert
```

Delete the certificate stack last: ACM won't delete a certificate that CloudFront is still
using.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Docker is installed but not running` | Start Docker Desktop and wait for it to show "running". |
| `sam build` is very slow or errors on Apple Silicon | Turn on Rosetta emulation in Docker Desktop (Step 1) and restart Docker. |
| `Unable to locate credentials` or `ExpiredToken` | Run `aws configure`, or `aws sso login` if you use SSO. Check `AWS_PROFILE` is set if you use a named profile. |
| `AccessDenied` for some service during deploy | Your user lacks a permission. Use an admin user or role for deploying. |
| Stack fails: `...decreases account's UnreservedConcurrentExecution below its minimum` | Rerun with `RESERVED_CONCURRENCY=0 ./deploy.sh` and request the quota increase (Step 3). |
| Certificate stack sits for more than 30 minutes | The domain's name servers don't match the hosted zone. Recheck Step 3. |
| `CNAMEAlreadyExists` | Another CloudFront distribution already claims that host name. Remove it from that distribution, or pick a different host name. |
| Site gives 403 or old content right after a deploy | CloudFront is still spreading the update. Wait 5–10 minutes and reload with Cmd+Shift+R. |
| Page says "The analyzer is busy right now" | That's the rate limit working. If real users hit it, raise `ApiRateLimit` and `ApiBurstLimit` in `template.yaml` and redeploy. |
| A very large log returns an error after about 30 seconds | API Gateway's 30-second limit. Untick some plot options and try again. |
| A stack ends in `ROLLBACK_COMPLETE` | Open CloudFormation → the stack → Events and find the first red `CREATE_FAILED` row for the cause. A stack in `ROLLBACK_COMPLETE` must be deleted before `./deploy.sh` can try again. |
