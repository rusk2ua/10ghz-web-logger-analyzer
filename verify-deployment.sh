#!/usr/bin/env bash
# Smoke-test a deployed stack: the site loads, the API processes the bundled
# sample log end to end, and a generated file downloads.
#
#   ./verify-deployment.sh            # uses the stack's WebsiteURL
#   ./verify-deployment.sh https://10ghz.microwavedx.com
set -euo pipefail
cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-2}"
STACK_NAME="${STACK_NAME:-arrl-10ghz-web}"
URL="${1:-}"
if [[ -z "$URL" ]]; then
  URL=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='WebsiteURL'].OutputValue" --output text 2>/dev/null) \
    || { echo "Stack $STACK_NAME not found in $REGION -- run ./deploy.sh first."; exit 1; }
fi
URL="${URL%/}"
echo "Testing $URL"

status=$(curl -s -o /dev/null -w '%{http_code}' "$URL/")
[[ "$status" == 200 ]] && echo "OK   site loads (HTTP 200)" || { echo "FAIL site returned HTTP $status"; exit 1; }

# Submit the sample log as a background job (with a path map, so grid-mapper
# is exercised too), poll until it finishes, then download one result.
python3 - "$URL" <<'EOF'
import json, ssl, sys, time, urllib.error, urllib.parse, urllib.request

# The python.org macOS installer doesn't use the system certificate store
# until "Install Certificates.command" is run, so prefer certifi's CA bundle
# (installed with requests in the venv) and fall back to the default store.
try:
    import certifi
    TLS = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    TLS = ssl.create_default_context()

def urlopen(req, timeout=30):
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=TLS)
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            sys.exit(f"FAIL TLS certificate check failed ({e.reason.verify_message}). With python.org "
                     "Python on macOS, run its 'Install Certificates.command', or activate the venv.")
        raise

url = sys.argv[1]
csv = open("tests/fixtures/sample_qso_log.csv").read()
body = json.dumps({"files": [{"name": "sample_qso_log.csv", "content": csv}], "callsign": "K2UA",
                   "outputs": ["cabrillo", "summary", "directional_viz", "grid_paths"]}).encode()

def call(path, data=None):
    req = urllib.request.Request(url + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")

status, submitted = call("/api/process", body)
if status != 202:
    sys.exit(f"FAIL /api/process returned HTTP {status}: {submitted}")
print(f"OK   job queued ({submitted['jobId']})")

started = time.time()
while True:
    status, job = call(f"/api/jobs/{submitted['jobId']}")
    if status != 200:
        sys.exit(f"FAIL /api/jobs returned HTTP {status}: {job}")
    if job["state"] in ("done", "error"):
        break
    if time.time() - started > 300:
        sys.exit(f"FAIL job still {job['state']} after 5 minutes")
    time.sleep(3)
if job["state"] == "error":
    sys.exit(f"FAIL job error: {job.get('message')}")

r = job["result"]
names = [f["name"] for f in r["files"]]
print(f"OK   job finished in {time.time() - started:.0f} s with {len(names)} files: {', '.join(names)}")
if r["errors"]:
    print("WARN", r["errors"])
if not any(f["output"] == "grid_paths" for f in r["files"]):
    sys.exit("FAIL no grid-mapper path maps were generated")
with urlopen(urllib.parse.urljoin(url + "/", r["files"][0]["url"])) as resp:
    assert resp.status == 200
    print(f"OK   download works ({resp.headers.get('Content-Type')}, {len(resp.read())} bytes)")
print(f"OK   running analyzer {r['upstream']} and grid-mapper {r.get('gridMapper')}")
EOF
