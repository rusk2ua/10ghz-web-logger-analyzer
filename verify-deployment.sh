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

body=$(python3 - <<'EOF'
import json
csv = open("tests/fixtures/sample_qso_log.csv").read()
print(json.dumps({"files": [{"name": "sample_qso_log.csv", "content": csv}],
                  "callsign": "K2UA", "outputs": ["cabrillo", "summary", "directional_viz"]}))
EOF
)
response=$(curl -s -w '\n%{http_code}' -X POST "$URL/api/process" \
  -H 'Content-Type: application/json' --data "$body")
status="${response##*$'\n'}"
json="${response%$'\n'*}"
if [[ "$status" != 200 ]]; then
  echo "FAIL /api/process returned HTTP $status: $json"
  exit 1
fi

python3 - "$json" <<'EOF'
import json, sys, urllib.request
r = json.loads(sys.argv[1])
assert r.get("success"), r
names = [f["name"] for f in r["files"]]
print(f"OK   API generated {len(names)} files: {', '.join(names)}")
if r["errors"]:
    print("WARN", r["errors"])
with urllib.request.urlopen(r["files"][0]["url"]) as resp:
    assert resp.status == 200
    print(f"OK   download works ({resp.headers.get('Content-Type')}, {len(resp.read())} bytes)")
print(f"OK   running upstream analyzer {r['upstream']}")
EOF
