#!/usr/bin/env bash
# Pull the upstream CLI projects into the web app so it runs exactly the same
# code as the command-line tools:
#
#   analyzer    rusk2ua/10ghz-log-analyzer -> backend/app/analyzer/
#   gridmapper  rusk2ua/grid-mapper        -> backend/app/gridmapper/
#
# Usage:
#   scripts/sync-upstream.sh                     # both projects, latest main
#   scripts/sync-upstream.sh analyzer v1.7.0     # one project at a tag/branch/commit
#   scripts/sync-upstream.sh gridmapper v1.6.0
#   scripts/sync-upstream.sh v1.7.0              # (old form) analyzer only
#   ANALYZER_DIR=../10ghz-log-analyzer GRIDMAPPER_DIR=../grid-mapper scripts/sync-upstream.sh
#                                                # use local checkouts instead of cloning
#
# The vendored files are copied verbatim -- never edit them here. Anything
# web-specific belongs in backend/app/runner.py. After syncing, compare the
# upstream-requirements.txt files with backend/requirements.txt, then run pytest.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# checkout <name> <repo-url> <ref> <local-dir-or-empty>  -> sets SRC and COMMIT
checkout() {
  local name="$1" url="$2" ref="$3" local_dir="$4"
  if [[ -n "$local_dir" ]]; then
    SRC="$(cd "$local_dir" && pwd)"
    COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
  else
    git clone --quiet "$url" "$WORK/$name"
    git -C "$WORK/$name" checkout --quiet "$ref"
    SRC="$WORK/$name"
    COMMIT="$(git -C "$SRC" rev-parse HEAD)"
  fi
}

# write_upstream <dest> <url> <ref> <version>
write_upstream() {
  cat > "$1/UPSTREAM.txt" <<EOF
repository: $2
ref: $3
commit: $COMMIT
version: $4
synced: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
}

sync_analyzer() {
  local ref="${1:-main}" url="${ANALYZER_URL:-https://github.com/rusk2ua/10ghz-log-analyzer.git}"
  local dest="$ROOT/backend/app/analyzer"
  local scripts=(data_source.py arrl_10ghz_cabrillo.py station_report.py weekend_analysis.py
                 comprehensive_analysis.py directional_visualization.py log_comparison.py)
  checkout analyzer "$url" "$ref" "${ANALYZER_DIR:-${UPSTREAM_DIR:-}}"
  mkdir -p "$dest" "$ROOT/tests/fixtures" "$ROOT/frontend/samples"
  for f in "${scripts[@]}"; do
    [[ -f "$SRC/$f" ]] || { echo "ERROR: $f not found in 10ghz-log-analyzer -- the script list needs updating." >&2; exit 1; }
    cp "$SRC/$f" "$dest/$f"
  done
  cp "$SRC/requirements.txt" "$dest/upstream-requirements.txt"
  cp "$SRC"/logs/sample_* "$ROOT/tests/fixtures/"
  cp "$SRC"/logs/sample_* "$ROOT/frontend/samples/"
  local version
  version="$(grep -oE 'Current version: \*\*v[0-9.]+' "$SRC/README.md" | grep -oE 'v[0-9.]+' || echo unknown)"
  write_upstream "$dest" "$url" "$ref" "$version"
  echo "Synced 10ghz-log-analyzer @ ${COMMIT:0:7} ($version) -> backend/app/analyzer/"
}

sync_gridmapper() {
  local ref="${1:-main}" url="${GRIDMAPPER_URL:-https://github.com/rusk2ua/grid-mapper.git}"
  local dest="$ROOT/backend/app/gridmapper"
  checkout gridmapper "$url" "$ref" "${GRIDMAPPER_DIR:-}"
  mkdir -p "$dest"
  [[ -f "$SRC/maidenhead_map.py" ]] || { echo "ERROR: maidenhead_map.py not found in grid-mapper." >&2; exit 1; }
  cp "$SRC/maidenhead_map.py" "$dest/maidenhead_map.py"
  cp "$SRC/requirements.txt" "$dest/upstream-requirements.txt"
  cp "$SRC/LICENSE" "$dest/LICENSE"
  local version
  version="v$(grep -oE '^## \[[0-9.]+\]' "$SRC/CHANGELOG.md" | head -1 | grep -oE '[0-9.]+' || echo unknown)"
  write_upstream "$dest" "$url" "$ref" "$version"
  echo "Synced grid-mapper @ ${COMMIT:0:7} ($version) -> backend/app/gridmapper/"
}

case "${1:-all}" in
  all)        sync_analyzer main; sync_gridmapper main ;;
  analyzer)   sync_analyzer "${2:-main}" ;;
  gridmapper) sync_gridmapper "${2:-main}" ;;
  *)          sync_analyzer "$1" ;;  # old form: a ref for the analyzer
esac

echo "Next: compare backend/app/*/upstream-requirements.txt with backend/requirements.txt, then run: pytest"
