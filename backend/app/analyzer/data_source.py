#!/usr/bin/env python3
"""
Shared data-source loading for the ARRL 10 GHz and Up Contest Logger scripts.

Every script in this project accepts a single optional command-line argument
(or, for log_comparison.py, 2-4 of them) that can be any of:

  - a path to a local Cabrillo .log file
  - a path to a local raw QSO .csv file, shaped like a Google Sheets export
    (date, band, sourcegrid, time, call, grid -- with repeated values in
    date/band/sourcegrid omitted on consecutive rows, exactly as Google
    Sheets logs are commonly kept)
  - a Google Sheets share URL (https://docs.google.com/spreadsheets/...)

If no argument is given, scripts auto-detect a .log file in the current
directory, then in logs/, before falling back to this project's default
(hardcoded) Google Sheets URL.

Raw QSO data is always normalized to these six columns:
    date, band, sourcegrid, time, call, grid

resolve_source() (and load_source_or_exit()) always return data that has
already been forward-filled (date/band/sourcegrid/time -- never call/grid,
which must always be explicit on every QSO) and cleaned of blank rows, and
band values have already been run through normalize_band() so they're
consistent no matter which source format they came from. Callers should NOT
call .ffill() themselves.
"""

import argparse
import glob
import os
from io import StringIO

import pandas as pd
import requests

RAW_COLUMNS = ['date', 'band', 'sourcegrid', 'time', 'call', 'grid']
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1UFbxzWJBpPdUEkfLhNA6csKbHaNypDmGeWpaeP-bQyA/edit?usp=sharing"
LOGS_DIR = "logs"

# Canonical band buckets for the ARRL 10 GHz and Up contest. Some adjacent
# microwave allocations share one band category -- e.g. the 75.5-81 GHz
# amateur allocation is commonly called "78 GHz" by operators but uses the
# Cabrillo band code "75G". Log data may carry any of: a bare number from a
# raw QSO sheet ("78"), a full name ("78 GHz"), or a Cabrillo code ("75G").
# This table is the single source of truth for mapping all of those to one
# canonical display name, Cabrillo code, and scoring multiplier -- every
# script imports normalize_band/band_to_cabrillo/band_multiplier from here
# instead of keeping its own copy, which is what let bands go missing or
# get mismatched between scripts in the first place.
BAND_BUCKETS = [
    # (display name, Cabrillo code, points-per-km multiplier, raw aliases)
    # Multipliers per ARRL 10 GHz and Up rules 5.2: 10 GHz x1, 24 GHz x2,
    # 47 GHz x3, 75 GHz x4, and 122 GHz and up x5.
    ("10 GHz",  "10G",  1,  ("10",)),
    ("24 GHz",  "24G",  2,  ("24",)),
    ("47 GHz",  "47G",  3,  ("47",)),
    ("78 GHz",  "75G",  4,  ("78", "75", "76")),
    ("122 GHz", "123G", 5,  ("122", "119", "120", "123")),
    ("142 GHz", "142G", 5,  ("142",)),
    ("241 GHz", "241G", 5,  ("241",)),
    ("300 GHz", "300G", 5,  ("300",)),
]

BAND_ORDER = [display for display, _, _, _ in BAND_BUCKETS]


def _build_band_lookup():
    lookup = {}
    for display, code, multiplier, aliases in BAND_BUCKETS:
        keys = set(aliases)
        keys.add(code.lower())
        keys.add(display.lower().replace(' ', ''))
        for alias in aliases:
            keys.add(f"{alias}g")
            keys.add(f"{alias}ghz")
        for key in keys:
            lookup[key] = (display, code, multiplier)
    return lookup


_BAND_LOOKUP = _build_band_lookup()


def _band_key(band):
    key = str(band).strip().lower().replace(' ', '')
    # A bare-number band column read by pandas with blank cells becomes
    # float ("10.0", "24.0") -- treat those like the bare number.
    if key.endswith('.0'):
        key = key[:-2]
    return key


def normalize_band(band):
    """Canonical display name for a band, e.g. '78 GHz' -- regardless of
    whether the input is a bare number ('78'), a Cabrillo code ('75G'), or
    already a full name ('78 GHz'). Falls back to the stripped input for an
    unrecognized value rather than guessing."""
    hit = _BAND_LOOKUP.get(_band_key(band))
    return hit[0] if hit else str(band).strip()


def band_to_cabrillo(band):
    """Cabrillo band code for a band, e.g. '75G'."""
    hit = _BAND_LOOKUP.get(_band_key(band))
    return hit[1] if hit else str(band).strip().upper()


def band_multiplier(band):
    """Points-per-km scoring multiplier for a band."""
    hit = _BAND_LOOKUP.get(_band_key(band))
    return hit[2] if hit else 1


def normalize_time(value):
    """Zero-padded 4-digit HHMM string for a QSO time.

    pandas reads a time column like 0930 as the integer 930, and when the
    column has any blank cells (e.g. a Google Sheets export before
    forward-fill) as a float like 1005.0. Every script expects 'HHMM', so
    normalize here once. Also accepts 'HH:MM'. Unparseable values are
    returned unchanged.
    """
    if value is None or (isinstance(value, float) and value != value):
        return value
    text = str(value).strip().replace(':', '')
    if text.endswith('.0'):
        text = text[:-2]
    return text.zfill(4) if text.isdigit() and len(text) <= 4 else text

SOURCE_HELP = (
    "Path to a local Cabrillo .log file, a local raw QSO .csv file (drop one "
    "in logs/), or a Google Sheets share URL. If omitted, auto-detects a "
    ".log file (current directory, then logs/), falling back to this "
    "project's default Google Sheets URL."
)


def is_google_sheets_url(source):
    """True if source looks like a Google Sheets share/edit URL."""
    return isinstance(source, str) and 'docs.google.com/spreadsheets' in source


def sheets_url_to_csv_url(sheet_url):
    """Convert a Google Sheets share URL into its CSV export URL."""
    sheet_id = sheet_url.split('/d/')[1].split('/')[0]
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"


def fetch_sheet_raw(sheet_url):
    """Fetch a Google Sheet as CSV and normalize it to RAW_COLUMNS.

    Matches this project's original sheet layout: the first row is consumed
    by pandas as a header, then two more leading rows are skipped before the
    real QSO data begins.
    """
    csv_url = sheets_url_to_csv_url(sheet_url)
    response = requests.get(csv_url)
    response.raise_for_status()
    df = pd.read_csv(StringIO(response.text))
    contact_data = df.iloc[2:].copy()
    contact_data.columns = RAW_COLUMNS
    return contact_data


def _normalize_header(col):
    return str(col).strip().lower().replace(' ', '').replace('_', '')


def load_local_raw_csv(path):
    """Load a local raw QSO CSV and normalize it to RAW_COLUMNS.

    Supports two layouts:
      - A clean header row containing date/band/sourcegrid/time/call/grid
        (in any order, any case, spaces/underscores ignored) -- this is the
        format of the sample file in logs/ and the recommended format for
        a hand-exported CSV.
      - The legacy raw Google Sheets export layout: no usable header, two
        leading rows skipped, columns in fixed order. This matches a direct
        "File > Download > .csv" export of the original sheet template.
    """
    df = pd.read_csv(path)
    normalized = {_normalize_header(c): c for c in df.columns}

    if set(RAW_COLUMNS).issubset(normalized.keys()):
        renamed = df.rename(columns={normalized[col]: col for col in RAW_COLUMNS})
        return renamed[RAW_COLUMNS].copy()

    # No recognizable header -- fall back to the legacy positional layout.
    contact_data = df.iloc[2:].copy()
    contact_data.columns = RAW_COLUMNS
    return contact_data


def parse_cabrillo_file(filename):
    """Parse a Cabrillo log file. Returns (DataFrame, callsign)."""
    qsos = []
    callsign = None

    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('CALLSIGN:'):
                callsign = line.split(':', 1)[1].strip().upper()
            elif line.startswith('QSO:'):
                parts = line.split()
                if len(parts) >= 8:
                    if callsign is None:
                        callsign = parts[5].upper()
                    qsos.append({
                        'date': parts[3],
                        'band': parts[1],
                        'sourcegrid': parts[6],
                        'time': parts[4],
                        'call': parts[7],
                        'grid': parts[8] if len(parts) > 8 else ''
                    })
    return pd.DataFrame(qsos), callsign or "UNKNOWN"


def _fill_and_clean(df):
    """Forward-fill only the columns that are conventionally left blank on
    repeat rows (date, band, sourcegrid, time) -- the common spreadsheet
    convention of omitting a value that hasn't changed since the previous
    QSO (including the clock minute, when two contacts land in the same
    minute). call and grid are deliberately NEVER forward-filled: a row
    with no call sign isn't a real QSO (often just a stray blank line from
    the spreadsheet), and forward-filling it would silently manufacture a
    phantom duplicate contact using the call/grid from the row above.
    """
    df = df.copy()
    fill_cols = [c for c in ('date', 'band', 'sourcegrid', 'time') if c in df.columns]
    if fill_cols:
        df[fill_cols] = df[fill_cols].ffill()
    if 'call' in df.columns:
        df = df.dropna(subset=['call'])
        df = df[df['call'].astype(str).str.strip() != '']
    if 'time' in df.columns:
        df['time'] = df['time'].apply(normalize_time)
    if 'band' in df.columns:
        # Normalize once, here, so every script downstream sees the same
        # canonical band name regardless of whether it came from a bare
        # number ("78"), a Cabrillo code ("75G"), or a full name.
        df['band'] = df['band'].apply(normalize_band)
    return df.reset_index(drop=True)


def find_local_log():
    """Look for a .log file in the current directory, then in logs/."""
    for pattern in ('*.log', os.path.join(LOGS_DIR, '*.log')):
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return None


def resolve_source(cli_arg=None, default_url=DEFAULT_SHEET_URL, verbose=True):
    """Resolve a data source into (DataFrame, callsign).

    cli_arg may be:
      - None: auto-detect a .log (current directory, then logs/), else use
        default_url
      - a Google Sheets share URL
      - a path to a local .log (Cabrillo) file
      - a path to a local .csv (raw QSO export) file
    """
    source = cli_arg

    if source is None:
        local_log = find_local_log()
        source = local_log if local_log else default_url

    if is_google_sheets_url(source):
        if verbose:
            print("Loading from Google Sheets...")
        df, callsign = fetch_sheet_raw(source), "UNKNOWN"
    elif not os.path.exists(source):
        raise FileNotFoundError(
            f"'{source}' is not a local file and not a recognized Google Sheets URL."
        )
    elif source.lower().endswith('.log'):
        if verbose:
            print(f"Loading Cabrillo file: {source}")
        df, callsign = parse_cabrillo_file(source)
    else:
        if verbose:
            print(f"Loading raw QSO CSV: {source}")
        df, callsign = load_local_raw_csv(source), "UNKNOWN"

    return _fill_and_clean(df), callsign


def load_source_or_exit(cli_arg=None, default_url=DEFAULT_SHEET_URL, verbose=True):
    """Like resolve_source, but prints a clean message and exits (rather
    than raising a traceback) if the source can't be found."""
    import sys
    try:
        return resolve_source(cli_arg, default_url=default_url, verbose=verbose)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)


def build_arg_parser(description):
    """Argparse parser for scripts that take a single optional source arg."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('source', nargs='?', default=None, help=SOURCE_HELP)
    return parser


def build_multi_source_arg_parser(description):
    """Argparse parser for scripts that compare 2-4 sources (log_comparison.py)."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        'sources',
        nargs='+',
        help=(
            "2 to 4 sources to compare. Each may be a local Cabrillo .log "
            "file, a local raw QSO .csv file (e.g. from logs/), or a "
            "Google Sheets share URL."
        )
    )
    return parser


def source_label(source):
    """Human-readable label for a source, for filenames/reports."""
    if is_google_sheets_url(source):
        return "GoogleSheet"
    return os.path.basename(source)
