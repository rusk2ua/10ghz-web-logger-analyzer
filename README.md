# ARRL 10 GHz and Up Contest Log Analyzer — Web Edition

A public web front end for K2UA's [10ghz-log-analyzer](https://github.com/rusk2ua/10ghz-log-analyzer)
and [grid-mapper](https://github.com/rusk2ua/grid-mapper). Anyone can upload a contest log
(Cabrillo or CSV) or paste a Google Sheets link and get:

- a **Cabrillo log** ready to submit, with claimed score
- the **contest summary** (score by band)
- **station activity**, **weekend**, and **comprehensive** analysis reports
- **directional polar plots**, per contest day or per operating location (rovers)
- a **comparison** of 2–4 logs
- **maps** from grid-mapper: path maps (a line to each station worked, with call and km) and
  grid-square density maps, per band and per operating location, optionally as interactive
  HTML and with an OpenStreetMap street underlay
- a single **.zip** with everything, plus the scoring breakdown and duplicate check

The web app runs both CLI projects' own scripts, vendored unmodified, so its scores,
reports and maps are identical to the command-line versions. Currently vendored:
10ghz-log-analyzer **v1.6.1** and grid-mapper **v1.5.0** (see `UPSTREAM.txt` in
`backend/app/analyzer/` and `backend/app/gridmapper/`).

**Live at [10ghz.microwavedx.com](https://10ghz.microwavedx.com)** · Web app version
**v2.3.1**. See the [version history](#version-history).

## Architecture

```
Browser ──► CloudFront (HTTPS, security headers, optional microwavedx.com cert)
              ├── /*        → S3 website bucket (private, Origin Access Control)
              └── /api/*    → API Gateway HTTP API (rate-limited)
                                 └── Lambda (container image: Python 3.12 + pandas/matplotlib/cartopy)
                                       │  POST /api/process: validate, queue a job, invoke itself async
                                       │  GET  /api/jobs/{id}: job status → the browser polls this
                                       ├── (background) runs the vendored 10ghz-log-analyzer and
                                       │   grid-mapper scripts — up to 15 minutes per job
                                       └── writes results → private S3 bucket (auto-deleted after 1 day)
                                                            downloaded via 1-hour presigned URLs
```

Analyses run as **background jobs** because maps for a big rover log can take minutes, well
past API Gateway's 30-second limit. `POST /api/process` validates the request (bad input still
gets an instant error), saves it, and has the Lambda invoke itself asynchronously; the page
polls `GET /api/jobs/{id}` every couple of seconds until the result is ready.

| Path | What it is |
|---|---|
| `frontend/` | Static site (HTML/CSS/JS, no build step) |
| `backend/app/handler.py` | Lambda entry point: validation, background jobs, packaging the results |
| `backend/app/runner.py` | Runs each upstream script's `main()` in a sandbox (argv, temp dir, `input()` answers) |
| `backend/app/analyzer/` | **Vendored** 10ghz-log-analyzer scripts. Don't edit these; run `scripts/sync-upstream.sh` |
| `backend/app/gridmapper/` | **Vendored** grid-mapper (`maidenhead_map.py`). Same rule |
| `backend/app/usage.py` | Anonymous usage records and the stats dashboard data |
| `backend/Dockerfile` | Lambda container image, including the Natural Earth map data |
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

pytest
python dev/local_server.py
```

`pytest` runs the test suite in under a minute (the first run also downloads ~36 MB of map data). The server runs at http://localhost:8000.

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

First create the certificate. It must be in us-east-1. The second command prints its ARN:

```bash
aws cloudformation deploy --region us-east-1 --stack-name arrl-10ghz-web-cert \
  --template-file certificate.yaml \
  --parameter-overrides DomainName=10ghz.microwavedx.com HostedZoneId=<your zone id>
aws cloudformation describe-stacks --region us-east-1 --stack-name arrl-10ghz-web-cert \
  --query "Stacks[0].Outputs[0].OutputValue" --output text
```

Then deploy the app with that certificate, and upload `frontend/` the way `deploy.sh` does:

```bash
sam build
sam deploy --parameter-overrides DomainName=10ghz.microwavedx.com \
  CertificateArn=<arn from step 1> HostedZoneId=<your zone id>
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
| `ALERT_EMAIL` | none | Email for the AWS Budgets alerts (both budgets need it) |
| `APP_BUDGET_USD` | 5 | Monthly budget for this app alone (tagged resources) |
| `MONTHLY_BUDGET_USD` | 10 | Whole-account safety-net budget |
| `RESERVED_CONCURRENCY` | 3, or 0 on small accounts | Max simultaneous Lambda runs |
| `KEEP_IMAGES` | 3 | Lambda image versions kept in ECR |
| `AWS_REGION` / `STACK_NAME` | us-east-2 / arrl-10ghz-web | Where to deploy |

API rate limits (1 request/s, burst 2) and result retention (1 day) are template
parameters. Change them in `template.yaml` or with `sam deploy --parameter-overrides`.

### Abuse protection and privacy

- API Gateway throttles `/api/process` (1 request/s, burst 2, across all users). One
  analysis is one request, so real users don't notice.
- Lambda reserved concurrency caps simultaneous runs at 3, which caps the worst-case cost of
  abuse at roughly $8/day. Accounts still on the 10-execution starter limit can't reserve
  any; `deploy.sh` detects that and tells you.
- Uploads are limited to 4 logs of 1 MB each; Google Sheets downloads to 2 MB.
- With `ALERT_EMAIL` set, two AWS Budgets alerts:
  - **App budget ($5/month):** covers only resources tagged `app=arrl-10ghz-web`, and emails
    when actual or forecast spend goes over $5. `deploy.sh` activates the `app` cost
    allocation tag it depends on. AWS may take up to 24 hours to discover a new tag, so a
    first deploy may need a rerun the next day.
  - **Account budget ($10/month):** a safety net for anything untagged. It emails at 80% of
    actual spend or when forecast spend goes over $10.
- Both S3 buckets are private. Results are reachable only through 1-hour presigned links and
  are deleted after a day. Uploaded logs never leave the Lambda's temp directory.
- Usage statistics are anonymous: no call signs, IP addresses or log contents are stored
  (see [Usage dashboard](#usage-dashboard)).
- CloudFront adds a strict Content-Security-Policy, HSTS, and anti-framing headers.

### Cost

At about 200 uses a year, this costs roughly **$0.10/month, or $1–2/year**. Map jobs use
more Lambda time (roughly 10–60 s each) but still fit comfortably in the free tier. Lambda,
CloudFront, S3 transfer and logs stay within AWS's permanent free tiers. The only steady
charge is ECR storage for the Lambda image (about 0.4 GB per version). `deploy.sh` applies a
lifecycle rule that keeps just the newest 3 images, so that cost can't grow. The Route 53
hosted zone ($0.50/month) and domain registration belong to the domain, not this app.

### Migrating from the old CDK version

The previous version was a CDK stack named `Arrl10GhzWebStack`. After the new stack is up,
remove the old one:

```bash
aws cloudformation delete-stack --region us-east-2 --stack-name Arrl10GhzWebStack
```

## Usage dashboard

**[10ghz.microwavedx.com/stats/](https://10ghz.microwavedx.com/stats/)** is an owner's dashboard
of how the site is used. It isn't linked from the site, and it's excluded from search engines
(`robots.txt` and a `noindex` tag). It isn't password-protected, but it shows only anonymous
aggregates.

It shows:

- **Headline numbers:** analyses (all time and the last 30 days), unique operators, home-page
  visits, visit-to-analysis rate, success rate, median log size, and processing time.
- **Activity over time:** visits and analyses per day for the last 180 days, plus a monthly
  table.
- **Feature use:** how often each output is requested, file upload vs. Google Sheets, Cabrillo
  vs. CSV, logs per analysis, and the band category chosen.
- **The logs themselves:** bands worked, QSO-count distribution, contest year, and the top
  4-character operating grids.
- **Operators:** repeat use (1, 2–3, or 4+ analyses per operator).
- **Problems:** rejected requests by reason, server errors, and outputs that failed.

**How it works**

1. **One record per request.** Each analysis writes a small JSON record to the private results
   bucket under `usage/` (`backend/app/usage.py`), and each home-page view sends an anonymous
   `POST /api/ping`.
2. **What's stored.** Call signs are stored only as a salted one-way hash, used for
   unique-operator counts. Visitors are counted by a hash of that day's IP and browser, which
   can't be linked across days. No log contents are ever stored.
3. **Daily rebuild.** Every day at 06:15 UTC the same Lambda, run on a schedule, compacts
   finished days into one file per day (`usage/daily/`) and publishes `stats/data.json` to
   the website bucket. `deploy.sh` also refreshes it after every deploy.
4. **Cost.** It stays within the free tiers: a few hundred small S3 objects a year and one
   short Lambda run a day.

To refresh it by hand:

```bash
aws lambda invoke --region us-east-2 --cli-binary-format raw-in-base64-out \
  --payload '{"action": "aggregate"}' \
  --function-name "$(aws cloudformation describe-stacks --region us-east-2 --stack-name arrl-10ghz-web --query "Stacks[0].Outputs[?OutputKey=='ProcessFunctionName'].OutputValue" --output text)" \
  /tmp/stats.json
```

Locally, `python dev/local_server.py` serves the dashboard at http://localhost:8000/stats/ and
rebuilds it on every load.

Statistics start from the first request after v2.2.0 is deployed. Nothing earlier was
recorded.

## Keeping up with the CLI projects

When `10ghz-log-analyzer` or `grid-mapper` gets a new release:

```bash
scripts/sync-upstream.sh
git diff backend/app/analyzer/ backend/app/gridmapper/
pytest
./deploy.sh
```

With no arguments the sync script refreshes both projects from `main`. To sync one project,
or pin a release, name it: `scripts/sync-upstream.sh analyzer v1.7.0` or
`scripts/sync-upstream.sh gridmapper v1.6.0`. Review the `git diff` of what changed
upstream; `pytest` includes the web-vs-CLI parity test and map tests.

If upstream adds a new script or command-line option, add it to the output tables at the
top of `backend/app/runner.py` and as a checkbox in `frontend/index.html`. If upstream adds
a dependency, add it (pinned) to `backend/requirements.txt`. If grid-mapper starts using
another Natural Earth dataset, add it to `backend/fetch_natural_earth.py` so it's baked into
the image.

## API

**1. Submit:** `POST /api/process` with JSON:

```json
{
  "files": [{"name": "k2ua.csv", "content": "date,band,sourcegrid,time,call,grid\n..."}],
  "sheetsUrl": "https://docs.google.com/spreadsheets/d/...",
  "callsign": "K2UA",
  "bandCategory": "AUTO",
  "outputs": ["cabrillo", "summary", "station_report", "weekend_analysis",
              "comprehensive_analysis", "directional_viz", "directional_location", "comparison",
              "grid_paths", "grid_density"],
  "mapHtml": false,
  "mapOsm": false
}
```

It returns HTTP 202 with `{"jobId": "..."}`. Input that can be checked immediately (no
outputs, bad call sign, oversized file) returns HTTP 400 with a readable `message` instead.

**2. Poll:** `GET /api/jobs/{jobId}` returns `{"state": "queued" | "running" | "done" | "error"}`.

- **`done`:** includes `result`, which lists each log's call sign, QSO count and bands, then
  download URLs for every generated file, a .zip, the processing notes, and any per-output
  errors.
- **`error`:** includes a readable `message`. That covers problems found while running, such as
  a log with no QSOs, and jobs that never finished.

## Known limitations

- Call signs with a `/` suffix (e.g. `K2UA/R`) aren't accepted, because the upstream
  scripts put the call sign in output file names. The 10 GHz and Up Contest has no rover
  category, so this only matters if support for the ARRL VHF contests is added later.
- A job can run for up to 15 minutes (the Lambda timeout). Even a big rover log with every map
  option fits comfortably, but a job that runs longer reports an error rather than a partial
  result.

## Version history

### v2.3.1 (2026-09-25): upper-band scoring fix

- **Band multipliers now match the ARRL rules (section 5.2):** 10 GHz ×1, 24 GHz ×2,
  47 GHz ×3, 75 GHz ×4, and 122 GHz and up ×5. The analyzer had scored 142 GHz at ×6 and
  241/300 GHz at ×10, which inflated the claimed score, summary, weekend analysis and
  directional plots for any log with QSOs on those bands. Fixed upstream in
  10ghz-log-analyzer v1.6.1 and synced here.
- Corrected the Scoring note at the bottom of the page.
- New `tests/test_scoring.py` pins the multiplier table to the rules.

### v2.3.0 (2026-09-25): grid-mapper maps and background jobs

- **Maps from [grid-mapper](https://github.com/rusk2ua/grid-mapper) v1.5.0**, vendored
  unmodified in `backend/app/gridmapper/` and run through the same sandboxed adapter:
  - **Path maps** (on by default): a line from your grid to each station, with call and km.
  - **Grid maps:** worked squares shaded by contact count.
  - Maps come one per band, and one per operating location for rovers.
  - Options for **interactive HTML** versions (downloadable, Leaflet-based) and an
    **OpenStreetMap street underlay**.
  - CSV and Google Sheets logs are converted to Cabrillo by the upstream converter first,
    so maps use the operating grid on every QSO.
- **Background jobs:** `POST /api/process` now validates and queues (HTTP 202 + `jobId`). The
  Lambda invokes itself asynchronously to do the work (timeout raised to 15 minutes, no
  automatic retries), and the page polls `GET /api/jobs/{id}` with a live status. That removes
  the 30-second API Gateway ceiling. Job documents live under `jobs/` in the results bucket
  and expire after a day, and the uploaded log is deleted as soon as its job finishes.
- **Lambda image:** adds cartopy, shapely, pyproj, scipy and Pillow (pinned), and bakes in the
  Natural Earth coastline/border data (~36 MB) so jobs never download it.
- **Other:**
  - `scripts/sync-upstream.sh` now syncs both upstream projects.
  - The stats dashboard counts map outputs and map options.
  - `verify-deployment.sh` submits and polls a job that includes a path map.
- 11 new tests (49 total).

### v2.2.1 (2026-09-25): fix "Access Denied" on /stats/

- CloudFront only serves `index.html` automatically at the site root, so `/stats/` asked S3
  for a nonexistent `stats/` object and got Access Denied. A small CloudFront Function
  (`DirectoryIndexFunction`, viewer-request on the website behavior) now serves
  `index.html` for any URL ending in `/`, and redirects `/stats` to `/stats/`.

### v2.2.0 (2026-09-25): usage dashboard

- New owner's dashboard at `/stats/`: activity over time, feature use, log characteristics,
  operating grids, repeat operators, and errors. It isn't linked from the site, and it's
  `noindex` and excluded in `robots.txt`.
- Anonymous usage recording (`backend/app/usage.py`):
  - one record per analysis;
  - a `POST /api/ping` per home-page view, with its own throttle of 5/s (burst 10);
  - salted hashes instead of call signs and IP addresses;
  - no log contents.
- The same Lambda runs on a daily 06:15 UTC schedule to compact records and publish
  `stats/data.json`. `deploy.sh` refreshes it after each deploy, and its site sync no longer
  deletes that file.
- The results bucket's one-day deletion rule now applies only to `results/`, so usage records
  are kept.
- Log metadata now includes 4-character operating grids.
- `dev/local_server.py` serves and rebuilds the dashboard locally, and honors
  `LOCAL_OUTPUT_DIR`.
- 12 new tests (38 total).

### v2.1.0 (2026-09-24): tighter cost controls

- **App budget:** a new $5/month AWS Budget covering only this app (resources tagged
  `app=arrl-10ghz-web`). It emails when actual or forecast spend goes over $5.
  `deploy.sh` activates the `app` cost allocation tag it relies on. The whole-account
  $10 budget stays as a safety net.
- **Image cleanup:** `deploy.sh` applies an ECR lifecycle rule that keeps only the newest 3
  Lambda images (`KEEP_IMAGES`), so image storage can't grow with every deploy. It also
  tags the SAM-created repository so the app budget counts it.
- **Lower limits:** the Lambda concurrency cap drops from 10 to 3, and the API rate limit
  from 2 requests/s (burst 5) to 1 request/s (burst 2). Worst-case abuse cost drops from
  about $28/day to about $8/day. Normal use is unaffected: one analysis is one request.
- New `ProcessFunctionName` stack output, used by `deploy.sh` to find the image repository.

### v2.0.0 (2026-09-24): rebuild on the upstream analyzer; live on microwavedx.com

A ground-up rebuild so the website runs the same code as the command-line analyzer.
([PR #1](https://github.com/rusk2ua/10ghz-web-logger-analyzer/pull/1))

**Analysis now matches the CLI exactly**
- The Lambda runs the unmodified
  [10ghz-log-analyzer](https://github.com/rusk2ua/10ghz-log-analyzer) **v1.6.0** scripts
  through a small adapter (`backend/app/runner.py`), replacing the hand-written copy of the
  analysis that had drifted from the CLI.
- Correct scoring: distance points × band multiplier, plus 100 points per unique call per
  band. The old version scored QSOs × bands.
- Bad or unreadable logs now return a clear error message. The old version quietly fell
  back to built-in sample data and reported success.
- `scripts/sync-upstream.sh` pulls in future CLI releases, and a test checks that the web
  output matches the CLI byte for byte.

**New features**
- All v1.6.0 outputs: Cabrillo log, contest summary, station, weekend and comprehensive
  reports, directional polar plots per contest day **and per operating location**, and
  2–4 log comparison.
- Band category setting (automatic, 10G or ALL).
- Download everything as one .zip, and view processing notes (scoring breakdown and
  duplicate check).
- Rewritten page: drag-and-drop upload, one-click sample logs, plot thumbnails, dark mode,
  and a phone-friendly layout.

**Infrastructure and security**
- AWS CDK (TypeScript) replaced by an AWS SAM / CloudFormation YAML template
  (`template.yaml`), with a container-image Lambda (Python 3.12).
- Private S3 buckets. The site is served through CloudFront Origin Access Control; results
  download through 1-hour links and are deleted after a day. Both buckets were previously
  public.
- Abuse and cost controls: API rate limiting, a Lambda concurrency cap, upload size limits,
  and an optional AWS Budgets email alert.
- Security headers (CSP, HSTS, frame denial). Download filenames are sanitized, fixing an
  issue found by Amazon Q review.
- Custom domain **10ghz.microwavedx.com**, with an ACM certificate created by
  `certificate.yaml` and DNS records added in Route 53 automatically.
- Fixed the site's JavaScript never having been committed (`.gitignore` excluded `*.js`).

**Tooling and docs**
- `deploy.sh` does the whole deploy in one command, and `verify-deployment.sh` smoke-tests
  it. `deploy.sh` also finds Docker Desktop's socket for SAM automatically.
- `dev/local_server.py` runs the full app locally without AWS, and 26 pytest tests cover
  it.
- Step-by-step guides: [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md) and
  [docs/AWS_DEPLOY.md](docs/AWS_DEPLOY.md), including fixes for common Mac problems (old
  system Python, Intel Homebrew on Apple Silicon, zsh pasting, the Docker socket).

### v1.0.0 (2025-09-26): first web version

- Serverless web front end: an S3 site behind CloudFront, API Gateway, and a Python Lambda,
  deployed with AWS CDK in us-east-2.
- Log upload or Google Sheets link. Call sign, grid square, contest year and band category
  detected automatically from the log.
- The analysis was a separate, simplified reimplementation of the CLI scripts, which was
  replaced in v2.0.0.
