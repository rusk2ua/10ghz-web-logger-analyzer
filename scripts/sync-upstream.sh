#!/usr/bin/env bash
# Pull the analysis scripts from the CLI project (rusk2ua/10ghz-log-analyzer)
# into backend/app/analyzer/ so the web app runs exactly the same code.
#
# Usage:
#   scripts/sync-upstream.sh            # latest commit on main
#   scripts/sync-upstream.sh v1.6.0     # a specific tag, branch, or commit
#   UPSTREAM_DIR=../10ghz-log-analyzer scripts/sync-upstream.sh   # a local checkout
#
# The vendored files are copied verbatim -- never edit them here. Anything
# web-specific belongs in backend/app/runner.py. After syncing, run the tests
# (pytest) before committing.
set -euo pipefail

REPO_URL="${UPSTREAM_URL:-https://github.com/rusk2ua/10ghz-log-analyzer.git}"
REF="${1:-main}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/backend/app/analyzer"

SCRIPTS=(
  data_source.py
  arrl_10ghz_cabrillo.py
  station_report.py
  weekend_analysis.py
  comprehensive_analysis.py
  directional_visualization.py
  log_comparison.py
)

if [[ -n "${UPSTREAM_DIR:-}" ]]; then
  SRC="$(cd "$UPSTREAM_DIR" && pwd)"
  COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
else
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT
  git clone --quiet "$REPO_URL" "$WORK/src"
  git -C "$WORK/src" checkout --quiet "$REF"
  SRC="$WORK/src"
  COMMIT="$(git -C "$SRC" rev-parse HEAD)"
fi

mkdir -p "$DEST"
for f in "${SCRIPTS[@]}"; do
  if [[ ! -f "$SRC/$f" ]]; then
    echo "ERROR: $f not found upstream -- the script list here needs updating." >&2
    exit 1
  fi
  cp "$SRC/$f" "$DEST/$f"
done
cp "$SRC/requirements.txt" "$DEST/upstream-requirements.txt"
mkdir -p "$ROOT/tests/fixtures" "$ROOT/frontend/samples"
cp "$SRC"/logs/sample_* "$ROOT/tests/fixtures/"
cp "$SRC"/logs/sample_* "$ROOT/frontend/samples/"

VERSION="$(grep -oE 'Current version: \*\*v[0-9.]+' "$SRC/README.md" | grep -oE 'v[0-9.]+' || echo unknown)"
cat > "$DEST/UPSTREAM.txt" <<EOF
repository: $REPO_URL
ref: $REF
commit: $COMMIT
version: $VERSION
synced: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

echo "Synced ${#SCRIPTS[@]} scripts from $REPO_URL @ ${COMMIT:0:7} ($VERSION)"
echo "Next: compare upstream-requirements.txt with backend/requirements.txt, then run: pytest"
