# ARRL 10 GHz and Up Contest Log Analyzer — Web Edition

A public web front end for [rusk2ua/10ghz-log-analyzer](https://github.com/rusk2ua/10ghz-log-analyzer).
Anyone can upload a contest log (Cabrillo or CSV) or paste a Google Sheets link and get:

- a **Cabrillo log** ready to submit, with claimed score
- the **contest summary** (score by band)
- **station activity**, **weekend**, and **comprehensive** analysis reports
- **directional polar plots**, per contest day or per operating location (rovers)
- a **comparison** of 2–4 logs
- a single **.zip** with everything, plus the scoring breakdown and duplicate check

The web app runs the CLI project's own scripts, vendored unmodified, so its scores and
reports are identical to the command-line version. Currently vendored: **v1.6.0** (see
`backend/app/analyzer/UPSTREAM.txt`).

## Architecture

```
Browser ──► CloudFront (HTTPS, security headers, optional microwavedx.com cert)
              ├── /*        → S3 website bucket (private, Origin Access Control)
              └── /api/*    → API Gateway HTTP API (rate-limited)
                                 └── Lambda (container image: Python 3.12 + pandas/matplotlib)
                                       ├── runs the vendored 10ghz-log-analyzer scripts
                                       └── writes results → private S3 bucket (auto-deleted after 1 day)
                                                            downloaded via 1-hour presigned URLs
```

| Path | What it is |
|---|---|
| `frontend/` | Static site (HTML/CSS/JS, no build step) |
| `backend/app/handler.py` | Lambda entry point: validates the request and packages the results |
| `backend/app/runner.py` | Runs each upstream script's `main()` in a sandbox (argv, temp dir, `input()` answers) |
| `backend/app/analyzer/` | **Vendored** upstream scripts. Don't edit these; run `scripts/sync-upstream.sh` |
| `backend/Dockerfile` | Lambda container image |
| `template.yaml` | AWS SAM / CloudFormation template for the whole app |
| `certificate.yaml` | ACM certificate for a custom domain (us-east-1) |
| `deploy.sh`, `verify-deployment.sh` | One-command deploy and a post-deploy smoke test |
| `dev/local_server.py` | Runs the full app locally, without AWS |
| `tests/` | pytest suite, including a web-vs-CLI parity test |

## Step-by-step guides

- [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md): run the app on your computer in a virtual environment
- [docs/AWS_DEPLOY.md](docs/AWS_DEPLOY.md): deploy to AWS, including the microwavedx.com domain

## Run it locally

Needs Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                         # 25 tests, ~10 s
python dev/local_server.py     # open http://localhost:8000
```

The local server serves `frontend/` and routes `POST /api/process` to the same Lambda
handler that runs in AWS. Generated files go to `./local-output/` instead of S3. Click the
**sample CSV log** link on the page for an instant demo.

## Deploy to AWS

### Prerequisites

- An AWS account with credentials configured (`aws configure` or `aws sso login`)
- [AWS CLI v2](https://aws.amazon.com/cli/)
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) (`brew install aws-sam-cli`)
- Docker Desktop running (SAM uses it to build the Lambda image, which it pushes to ECR for you).
  On Apple Silicon Macs the image is built for x86_64 under emulation, which is slower but works.

### Deploy with the default CloudFront URL

```bash
./deploy.sh
./verify-deployment.sh
```

The app deploys to **us-east-2** as the stack `arrl-10ghz-web`. `deploy.sh` builds the
image, deploys `template.yaml`, uploads `frontend/`, invalidates CloudFront, and prints
the URL. Re-run it any time to ship changes.

### Deploy on microwavedx.com

Because microwavedx.com is registered in Route 53, the hosted zone already exists and
`deploy.sh` can do everything:

```bash
DOMAIN_NAME=10ghz.microwavedx.com \
ALERT_EMAIL=you@example.com \
./deploy.sh
```

That run:

1. finds the Route 53 hosted zone for microwavedx.com.
2. deploys `certificate.yaml` to **us-east-1** (CloudFront only accepts certificates from
   there) as the stack `arrl-10ghz-web-cert`. ACM validates it automatically through a DNS
   record that CloudFormation adds to your zone, which usually takes 2–10 minutes on the
   first run.
3. deploys the app with that certificate attached to CloudFront, and creates Route 53
   **A and AAAA alias records** pointing the host name at the distribution.

To serve the bare domain and `www` instead:

```bash
DOMAIN_NAME=microwavedx.com ALT_DOMAIN_NAME=www.microwavedx.com ./deploy.sh
```

<details>
<summary>Doing the certificate and DNS steps by hand (CLI or console)</summary>

**CLI, CloudFormation only:**

```bash
# 1. Certificate (must be us-east-1)
aws cloudformation deploy --region us-east-1 --stack-name arrl-10ghz-web-cert \
  --template-file certificate.yaml \
  --parameter-overrides DomainName=10ghz.microwavedx.com HostedZoneId=<your zone id>
aws cloudformation describe-stacks --region us-east-1 --stack-name arrl-10ghz-web-cert \
  --query "Stacks[0].Outputs[0].OutputValue" --output text      # → certificate ARN

# 2. App, with the certificate
sam build
sam deploy --parameter-overrides DomainName=10ghz.microwavedx.com \
  CertificateArn=<arn from step 1> HostedZoneId=<your zone id>
# then upload frontend/ as in deploy.sh
```

**Console:**

1. **Route 53 → Hosted zones → microwavedx.com**: copy the *Hosted zone ID*.
2. Switch the region to **N. Virginia (us-east-1)**. **Certificate Manager → Request →
   Request a public certificate**: enter `10ghz.microwavedx.com`, choose DNS validation,
   and request it. Open the certificate, click **Create records in Route 53**, and wait for
   *Issued*. Copy its ARN.
3. Deploy with `CertificateArn=<that ARN>` and `DomainName=...`. Pass `HostedZoneId` to have
   the stack create the DNS records, or leave it out and in Route 53 create an **A record →
   Alias → CloudFront distribution** (and an AAAA record the same way) yourself.

</details>

### Deploy settings

All are optional environment variables for `deploy.sh`:

| Variable | Default | Purpose |
|---|---|---|
| `DOMAIN_NAME` / `ALT_DOMAIN_NAME` | none | Custom host name(s) |
| `HOSTED_ZONE_ID` | auto-lookup | Route 53 zone, if the lookup can't find it |
| `CERT_ARN` | created | Use an existing us-east-1 certificate |
| `ALERT_EMAIL` | none | Email for the AWS Budgets alert |
| `MONTHLY_BUDGET_USD` | 10 | Budget threshold (whole account) |
| `RESERVED_CONCURRENCY` | 10, or 0 on small accounts | Max simultaneous Lambda runs |
| `AWS_REGION` / `STACK_NAME` | us-east-2 / arrl-10ghz-web | Where to deploy |

API rate limits (2 requests/s, burst 5) and result retention (1 day) are template
parameters. Change them in `template.yaml` or with `sam deploy --parameter-overrides`.

### Abuse protection and privacy

- API Gateway throttles `/api/process` (2 req/s, burst 5, across all users).
- Lambda reserved concurrency caps simultaneous runs (and so cost). Accounts still on the
  10-execution starter limit can't reserve any; `deploy.sh` detects that and tells you.
- Uploads are limited to 4 logs of 1 MB each; Google Sheets downloads to 2 MB.
- Optional AWS Budgets email when spend passes 80% of the budget, or is forecast to exceed it.
- Both S3 buckets are private. Results are reachable only through 1-hour presigned links and
  are deleted after a day. Uploaded logs never leave the Lambda's temp directory.
- CloudFront adds a strict Content-Security-Policy, HSTS, and anti-framing headers.

### Cost

For amateur-radio traffic this should stay within the free tier or cost cents per month:
Lambda runs about 2–10 s at 2 GB per log, and there are small S3, API Gateway, CloudFront,
and ECR storage charges (~$0.05/month for the image).

### Migrating from the old CDK version

The previous version was a CDK stack named `Arrl10GhzWebStack`. After the new stack is up,
remove the old one:

```bash
aws cloudformation delete-stack --region us-east-2 --stack-name Arrl10GhzWebStack
```

## Keeping up with the CLI project

When `10ghz-log-analyzer` gets a new release:

```bash
scripts/sync-upstream.sh            # or: scripts/sync-upstream.sh v1.7.0
git diff backend/app/analyzer/      # review what changed
pytest                              # includes a web-vs-CLI parity test
./deploy.sh
```

If upstream adds a new script or command-line option, add it to the output tables at the
top of `backend/app/runner.py` and as a checkbox in `frontend/index.html`. If upstream adds
a dependency, add it (pinned) to `backend/requirements.txt`.

## API

`POST /api/process` with JSON:

```json
{
  "files": [{"name": "k2ua.csv", "content": "date,band,sourcegrid,time,call,grid\n..."}],
  "sheetsUrl": "https://docs.google.com/spreadsheets/d/...",
  "callsign": "K2UA",
  "bandCategory": "AUTO",
  "outputs": ["cabrillo", "summary", "station_report", "weekend_analysis",
              "comprehensive_analysis", "directional_viz", "directional_location", "comparison"]
}
```

The response lists each log's call sign, QSO count and bands, then download URLs for every
generated file, a .zip, the processing notes, and any per-output errors. Invalid input returns
HTTP 400 with a readable `message`.

## Known limitations

- Call signs with a `/` suffix (e.g. `K2UA/R`) aren't accepted yet. The upstream scripts use
  the call sign in output file names.
- Processing must finish within API Gateway's 30-second limit. That's plenty for a contest
  log (the sample takes about 1 s), but a huge log with every plot type could get close.
