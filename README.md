# ARRL 10 GHz and Up / VHF Contest Log Analyzer — Web Edition

A public web front end for K2UA's [10ghz-log-analyzer](https://github.com/rusk2ua/10ghz-log-analyzer)
and [grid-mapper](https://github.com/rusk2ua/grid-mapper). It runs as two sites from this one
codebase:

- **[10ghz.microwavedx.com](https://10ghz.microwavedx.com)** for the ARRL 10 GHz and Up Contest.
- **[vhf.microwavedx.com](https://vhf.microwavedx.com)** for the ARRL January, June and September
  VHF Contests, built for rovers. The contest is detected from the log (or chosen); the entry
  category (Classic/Limited/Unlimited Rover, single-op, 3-Band, FM-Only, multi-op, Analog Only)
  decides the scoring; and a *No contest* choice maps any log, such as a home-station log,
  without a score. See [The VHF site](#the-vhf-site).

Anyone can upload a contest log (Cabrillo, ADIF or CSV) or paste a Google Sheets link and get:

- a **Cabrillo log** ready to submit, with claimed score
- the **contest summary** (score by band)
- **station activity**, **weekend**, and **comprehensive** analysis reports
- **directional polar plots**, per contest day or per operating location (rovers)
- a **comparison** of 2–4 logs
- **maps** from grid-mapper: path maps (a line to each station worked, with call and km) and
  grid-square density maps, per band and per operating location, optionally as interactive
  HTML and with an OpenStreetMap street underlay
- a **contest movie**: the whole log replayed as an animated map that follows the operator
  from location to location and draws in each contact by band, playable in any browser
- a single **.zip** with everything, plus the scoring breakdown and duplicate check

The web app runs both CLI projects' own scripts, vendored unmodified, so its scores,
reports and maps are identical to the command-line versions. Currently vendored:
10ghz-log-analyzer **v1.7.0** and grid-mapper **v1.7.1** (see `UPSTREAM.txt` in
`backend/app/analyzer/` and `backend/app/gridmapper/`).

**Live at [10ghz.microwavedx.com](https://10ghz.microwavedx.com)** · Web app version
**v2.5.2**. See the [version history](#version-history).

## The VHF site

The stack parameter `SiteMode` picks the site: `10ghz` (home page `frontend/index.html`) or
`vhf` (home page `frontend/vhf.html`). Both sites share the Lambda, `app.js` and the styles;
the VHF one passes `contest`, `category` and `analog` with each request (see [API](#api)).

- **Contests:** the ARRL January, June and September VHF Contests, scored by the analyzer's
  `vhf_cabrillo.py` (Jan/Jun/Sep VHF rules v1.2), plus ARRL 10 GHz and Up logs.
- **Detection:** the log's `CONTEST:` line (or ADIF `CONTEST_ID`), else its dates. The results
  say what was detected; the dropdown overrides it.
- **No contest:** maps and the movie without a score, for any log.
- **Outputs:** Cabrillo, summary (with rover stops and grids activated), station report, log
  comparison, path and grid maps, and the contest movie, which shades each grid a rover activates.
  The weekend, comprehensive and directional reports score with the 10 GHz rules, so they're
  only on the 10 GHz site.

Deploy it as its own stack, next to the 10 GHz one:

```bash
SITE_MODE=vhf STACK_NAME=arrl-vhf-web DOMAIN_NAME=vhf.microwavedx.com ./deploy.sh
STACK_NAME=arrl-vhf-web ./verify-deployment.sh
```

It gets its own certificate, buckets, rate limits, budgets and usage dashboard, and its
own cost tag (`app=arrl-vhf-web`), so its app budget and Cost Explorer show the VHF site's
spend alone. Run it locally with `SITE_MODE=vhf python dev/local_server.py`.

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

Needs Python 3.12 -- the same version as the Lambda image. Newer Pythons (3.14 and up)
can segfault with the pinned numpy/pandas versions. On a Mac: `brew install python@3.12`,
then create the venv with `python3.12` instead of `python3`.

```bash
python3.12 -m venv .venv
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
| `ALERT_EMAIL` | none (an existing stack's is reused) | Email for the AWS Budgets alerts (both budgets need it). A different address than the stack's is refused, since changing it would replace the budgets |
| `APP_BUDGET_USD` | 5 | Monthly budget for this app alone (tagged resources) |
| `MONTHLY_BUDGET_USD` | 10 | Whole-account safety-net budget |
| `RESERVED_CONCURRENCY` | 3, or 0 on small accounts | Max simultaneous Lambda runs |
| `KEEP_IMAGES` | 3 | Lambda image versions kept in ECR |
| `APP_TAG` | arrl-10ghz-web, or arrl-vhf-web with `SITE_MODE=vhf` | Value of the `app` cost tag on the stack's resources; the app budget counts only those |
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
  - **App budget ($5/month):** covers only resources tagged with the site's own cost tag
    (`app=arrl-10ghz-web` or `app=arrl-vhf-web`), and emails
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
  "contest": "AUTO",
  "category": "AUTO",
  "analog": false,
  "outputs": ["cabrillo", "summary", "station_report", "weekend_analysis",
              "comprehensive_analysis", "directional_viz", "directional_location", "comparison",
              "grid_paths", "grid_density"],
  "mapHtml": false,
  "mapOsm": false
}
```

`bandCategory` (`AUTO`, `10G` or `ALL`) is for the 10 GHz site. `contest` (`AUTO`, `NONE`,
`ARRL-VHF-JAN`, `ARRL-VHF-JUN`, `ARRL-VHF-SEP` or `ARRL-10-GHZ`), `category` (`AUTO`, `SO`, `SOP`,
`SO3B`, `SOFM`, `R`, `RL`, `RU`, `LM` or `UM`) and `analog` are for the VHF site, where each log in
the result also carries `contest`, `contestHow` (what it was detected from, or `chosen`) and
`category`. Files may be Cabrillo, ADIF or CSV text.

It returns HTTP 202 with `{"jobId": "..."}`. Input that can be checked immediately (no
outputs, bad call sign, oversized file) returns HTTP 400 with a readable `message` instead.

**2. Poll:** `GET /api/jobs/{jobId}` returns `{"state": "queued" | "running" | "done" | "error"}`.

- **`done`:** includes `result`, which lists each log's call sign, QSO count and bands, then
  download URLs for every generated file, a .zip, the processing notes, and any per-output
  errors.
- **`error`:** includes a readable `message`. That covers problems found while running, such as
  a log with no QSOs, and jobs that never finished.

## Known limitations

- On the VHF site, the directional plots and the weekend and comprehensive reports aren't
  offered: they score with the 10 GHz and Up rules.
- Uploads are limited to 1 MB per log (about 3,000 ADIF QSOs), plenty for a contest log but
  not for years of home-station logging.
- A job can run for up to 15 minutes (the Lambda timeout). Even a big rover log with every map
  option fits comfortably, but a job that runs longer reports an error rather than a partial
  result.

## Version history

### v2.5.2 (2026-11-07): a credit line on every image and HTML page

- Every image and HTML page the site makes now says **Created using
  vhf.microwavedx.com by K2UA** (or 10ghz.microwavedx.com on the 10 GHz site):
  - **Images** (path and grid maps, directional plots): a thin strip below the picture,
    small grey type at the right, so it never covers the map or plot. The same text is
    in the PNG's `Software` and `Comment` metadata.
  - **The contest movie and the interactive maps:** in the map attribution corner,
    after "Leaflet | Natural Earth" (grid-mapper v1.7.1's new `--credit`).
- Text files, Cabrillo logs and the .zip are unchanged for now.

### v2.5.1 (2026-11-07): a cost tag per site

- The VHF stack now tags its resources `app=arrl-vhf-web` (the 10 GHz stack keeps
  `app=arrl-10ghz-web`), and each stack's app budget filters on its own tag, so each budget
  and Cost Explorer show one site's spend. `deploy.sh` picks the tag from `SITE_MODE`
  (override with `APP_TAG`) and applies it to the stack, its certificate stack and its ECR
  image repository. The budget filter updates in place.
- Template parameter `AppTag` in `template.yaml` and `certificate.yaml`.

### v2.5.0 (2026-11-06): the VHF site, ADIF logs and rover call signs

- **vhf.microwavedx.com.** A second site from this codebase (`SiteMode=vhf`) for the ARRL
  January, June and September VHF Contests (analyzer v1.7.0, grid-mapper v1.7.0):
  - contest detection, with a dropdown to override it or to pick *No contest*;
  - every entry category, including Classic, Limited and Unlimited Rover, plus Analog Only;
  - a summary with rover stops and grids activated;
  - a movie that scores the contest and shades each grid as it's activated.
- **ADIF logs** (`.adi`, `.adif`) on both sites.
- **Rover call signs** such as `K2UA/R` are accepted (files are named `K2UA-R_...`).
- `deploy.sh` takes `SITE_MODE=vhf` (and refuses to deploy it over the 10 GHz stack);
  `verify-deployment.sh` checks either site; `dev/local_server.py` serves either.
- Tests: `tests/test_vhf_site.py` (17 tests).

### v2.4.3 (2026-11-06): running score in the contest movie

- The contest movie's info panel shows **Cumulative Score**, climbing with every contact
  (grid-mapper v1.6.2). It's scored exactly like the analyzer's summary, so it ends on the
  same claimed score: distance (km, rounded up, minimum 1) × band multiplier, plus 100 per
  unique call per band.
- New test: the movie's final score equals the analyzer's `calculate_score` for both sample
  logs (Cabrillo and CSV).

### v2.4.2 (2026-11-06): footer tagline

- A tagline at the bottom of the page: the site and its code are provided as a service to the
  Amateur Radio microwave community by Rus Healy, K2UA, with an invitation to share it and a
  reminder that mentoring new hams is the future of the hobby.

### v2.4.1 (2026-11-05): saved movies play offline

- **A downloaded movie no longer shows "Access blocked".** With the street underlay
  on, OpenStreetMap's tile servers refuse requests from a page opened as a local file
  (it sends no `Referer`) and draw "Access blocked" tiles. From grid-mapper v1.6.1:
  a movie or interactive map opened from a file leaves the street layer out and says
  so in its corner; opened from the site, it shows the streets as before.
- **The movie carries its own map engine** (Leaflet, bundled by grid-mapper in
  `vendor/leaflet/`), so the copy in "Download everything (.zip)" plays on a computer
  with no internet at all. About 160 KB more per movie.
- `scripts/sync-upstream.sh` now also copies grid-mapper's `vendor/` folder.

### v2.4.0 (2026-11-05): contest movie

- **New output: Contest movie**, from [grid-mapper](https://github.com/rusk2ua/grid-mapper)
  v1.6.0's `--animate`. The whole log replays as an animated map: it follows the grid in the
  sent exchange from one operating location to the next and draws a line to each station as
  it was worked, with a fixed color, dash and marker per band. Every operating day plays on
  one timeline (both weekends of the 10 GHz and Up contest as one four-day period), with long
  breaks and drives fast-forwarded. Controls: 0.5×/1×/2× speed, a draggable timeline, fixed
  Region/Area/Local zoom, band toggles and a table of operating locations. Works on phones.
- **It plays instead of downloading.** The movie is stored with `Content-Disposition: inline`,
  so the "▶ Watch" link opens it in a new tab (on a phone, a downloaded `.html` file opens in a
  preview that doesn't run it). Every other file still downloads; the .zip includes the movie.
- It gets its own grid-mapper run (`--animate-only`), so it doesn't also draw every per-band
  map. The OpenStreetMap street underlay option applies to it too.
- The Lambda image now also bakes Natural Earth's 10m state/province lines, which the movie
  draws, so no map data is downloaded during a job.
- `verify-deployment.sh` also requests a movie and checks it opens in the browser.
- Usage dashboard: "Contest movie" is counted with the other outputs.

### v2.3.5 (2026-10-31): deploy.sh keeps the budget alert email

- **`deploy.sh` reuses the stack's existing `ALERT_EMAIL`** when you leave it out, and stops
  before building if you pass a different one. The address is part of the two budgets;
  changing it made CloudFormation replace them, which fails because they have fixed names
  ("a budget with the same name but a different internalId already exists"), so the whole
  update rolled back. This happened when the address was typed with different dots
  (`rus.healy@` vs `rushealy@`: the same Gmail inbox, but different text).
- New `tests/test_deploy_script.py` runs that part of the script against a fake `aws`.
- The API throttle stays at the template defaults (1 request/s, burst 2).

### v2.3.4 (2026-10-31): street underlay on every band

- **The OpenStreetMap street underlay now works on every band at 902 MHz and up.** It was
  silently skipped for many Cabrillo band labels (`1.2G`, `2.3G`, `3.4G`, `5.7G`, `134G`,
  `241G`, `300G`), so those maps came out without it even when *OpenStreetMap street
  underlay* was ticked. The 1°×2° grid-square outlines were missing on the same bands. Fixed
  upstream in grid-mapper v1.5.1 and synced here. 10 GHz maps were never affected.
- grid-mapper v1.5.1 also starts the 2 mm band at 134 GHz and recognizes the 134, 241 and
  300 GHz Cabrillo codes.
- New test in `tests/test_jobs_maps.py` builds maps for 2 m, 5.7 GHz and 134 GHz with the
  underlay on and checks that only the microwave maps get it.
- README intro corrected to the vendored versions (analyzer v1.6.2, grid-mapper v1.5.1).

### v2.3.3 (2026-10-30): 134 GHz band name

- **The "142 GHz" band is now 134 GHz** (Cabrillo `134G`), matching the actual amateur
  allocation and the ARRL contest band list. Logs that still say `142` are mapped to
  134 GHz, so they keep scoring at ×5. Fixed upstream in 10ghz-log-analyzer v1.6.2 and
  synced here.
- `tests/test_scoring.py` now checks the 134 GHz band and the legacy `142` alias.
- **CI:** a GitHub Actions workflow (`.github/workflows/tests.yml`) runs cfn-lint and
  `pytest` on Python 3.12 for every push to `main` and every pull request. It doesn't deploy.
- Local setup now calls for Python 3.12 to match the Lambda image.

### v2.3.2 (2026-09-25): sturdier deployment check

- `verify-deployment.sh` now uses the `certifi` CA bundle when it's installed. python.org
  Python on macOS doesn't use the system certificates until *Install Certificates.command*
  is run, which made the API checks fail with `CERTIFICATE_VERIFY_FAILED`. If certificate
  checking still fails, it prints the fix instead of a traceback.
- It also resolves relative download links, so it can smoke-test the local dev server:
  `./verify-deployment.sh http://127.0.0.1:8000`.

### v2.3.1 (2026-09-25): upper-band scoring fix

- **Band multipliers now match the ARRL rules (section 5.2):** 10 GHz ×1, 24 GHz ×2,
  47 GHz ×3, 75 GHz ×4, and 122 GHz and up ×5. The analyzer had scored 142 GHz (now 134 GHz, see v2.3.3) at ×6 and
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
