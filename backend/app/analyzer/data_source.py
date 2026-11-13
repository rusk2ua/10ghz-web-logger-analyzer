#!/usr/bin/env python3
"""
Shared data-source loading for the ARRL 10 GHz and Up and VHF Contest Logger
scripts.

Every script in this project accepts a single optional command-line argument
(or, for log_comparison.py, 2-4 of them) that can be any of:

  - a path to a local Cabrillo .log file
  - a path to a local ADIF .adi/.adif file (from N1MM+, WSJT-X, LoTW, ...)
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
Cabrillo and ADIF logs also carry a 'mode' column (CW, PH, FM, DG, FT8, ...).

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
import re
from io import StringIO

import pandas as pd
import requests

RAW_COLUMNS = ['date', 'band', 'sourcegrid', 'time', 'call', 'grid']
DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1UFbxzWJBpPdUEkfLhNA6csKbHaNypDmGeWpaeP-bQyA/edit?usp=sharing"
LOGS_DIR = "logs"

# Canonical band buckets, 50 MHz and up. Some adjacent microwave allocations
# share one band category -- e.g. the 75.5-81 GHz amateur allocation is
# commonly called "78 GHz" by operators but uses the Cabrillo band code
# "75G". Log data may carry any of: a bare number from a raw QSO sheet
# ("78", "144"), a full name ("78 GHz"), a Cabrillo code ("75G", "1.2G"), or
# an ADIF band name ("4mm", "2m"). This table is the single source of truth
# for mapping all of those to one canonical display name and Cabrillo code
# -- every script imports normalize_band/band_to_cabrillo/band_multiplier
# from here instead of keeping its own copy, which is what let bands go
# missing or get mismatched between scripts in the first place.
#
# Bare numbers don't collide between the MHz and GHz bands: 50/144/222/432/
# 902 are only ever MHz bands, and 10/24/47/78/... only ever GHz bands, so a
# "24" in a spreadsheet still means 24 GHz.
BAND_BUCKETS = [
    # (display name, Cabrillo code, ARRL 10 GHz and Up points-per-km
    #  multiplier or None if the band doesn't count in that contest,
    #  low edge in MHz, raw aliases)
    # Multipliers per ARRL 10 GHz and Up rules 5.2: 10 GHz x1, 24 GHz x2,
    # 47 GHz x3, 75 GHz x4, and 122 GHz and up x5.
    ("50 MHz",  "50",   None, 50,     ("50", "6m", "50mhz")),
    ("144 MHz", "144",  None, 144,    ("144", "2m", "144mhz")),
    ("222 MHz", "222",  None, 222,    ("222", "220", "1.25m", "222mhz")),
    ("432 MHz", "432",  None, 420,    ("432", "440", "70cm", "432mhz")),
    ("902 MHz", "902",  None, 902,    ("902", "903", "33cm", "902mhz")),
    ("1.2 GHz", "1.2G", None, 1240,   ("1.2", "1.2g", "1.2ghz", "1296", "23cm")),
    ("2.3 GHz", "2.3G", None, 2300,   ("2.3", "2.3g", "2.3ghz", "2304", "13cm")),
    ("3.4 GHz", "3.4G", None, 3300,   ("3.4", "3.4g", "3.4ghz", "3456", "9cm")),
    ("5.7 GHz", "5.7G", None, 5650,   ("5.7", "5.7g", "5.7ghz", "5760", "6cm")),
    ("10 GHz",  "10G",  1,    10000,  ("10", "10g", "10ghz", "3cm")),
    ("24 GHz",  "24G",  2,    24000,  ("24", "24g", "24ghz", "1.25cm")),
    ("47 GHz",  "47G",  3,    47000,  ("47", "47g", "47ghz", "6mm")),
    ("78 GHz",  "75G",  4,    75500,  ("78", "75", "76", "78g", "75g", "76g", "78ghz", "75ghz", "76ghz", "4mm")),
    ("122 GHz", "123G", 5,    119980, ("122", "119", "120", "123", "122g", "119g", "120g", "123g",
                                       "122ghz", "119ghz", "120ghz", "123ghz", "2.5mm")),
    # "142" kept as an alias for logs made before v1.6.2
    ("134 GHz", "134G", 5,    134000, ("134", "142", "134g", "142g", "134ghz", "142ghz", "2mm")),
    ("241 GHz", "241G", 5,    241000, ("241", "241g", "241ghz", "1mm")),
    ("300 GHz", "300G", 5,    300000, ("300", "300g", "300ghz", "submm", "light")),
]

BAND_ORDER = [display for display, _, _, _, _ in BAND_BUCKETS]
# The bands the ARRL 10 GHz and Up contest counts.
TEN_GHZ_BANDS = [display for display, _, mult, _, _ in BAND_BUCKETS if mult]


def _build_band_lookup():
    lookup = {}
    for display, code, multiplier, _, aliases in BAND_BUCKETS:
        keys = set(aliases)
        keys.add(code.lower())
        keys.add(display.lower().replace(' ', ''))
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


def band_from_freq_mhz(freq_mhz):
    """Display name of the band a frequency in MHz falls in (50 MHz and up),
    or None. Used for ADIF FREQ and for Cabrillo logs that put the
    frequency in kHz in the band field."""
    try:
        freq = float(freq_mhz)
    except (TypeError, ValueError):
        return None
    if freq < 50:
        return None
    found = None
    for display, _, _, low, _ in BAND_BUCKETS:
        if freq >= low:
            found = display
    return found


def _lookup_band(band):
    hit = _BAND_LOOKUP.get(_band_key(band))
    if hit:
        return hit
    # A Cabrillo frequency in kHz, e.g. "144205" or "10368100".
    key = _band_key(band)
    if key.isdigit() and int(key) >= 50000:
        display = band_from_freq_mhz(int(key) / 1000)
        if display:
            return _BAND_LOOKUP[display.lower().replace(' ', '')]
    return None


def normalize_band(band):
    """Canonical display name for a band, e.g. '78 GHz' -- regardless of
    whether the input is a bare number ('78'), a Cabrillo code ('75G'), an
    ADIF band name ('4mm'), or already a full name ('78 GHz'). Falls back to
    the stripped input for an unrecognized value rather than guessing."""
    hit = _lookup_band(band)
    return hit[0] if hit else str(band).strip()


def band_to_cabrillo(band):
    """Cabrillo band code for a band, e.g. '75G'."""
    hit = _lookup_band(band)
    return hit[1] if hit else str(band).strip().upper()


def band_multiplier(band):
    """ARRL 10 GHz and Up points-per-km multiplier for a band: 0 for a band
    that contest doesn't count (anything below 10 GHz), 1 for a value that
    isn't a recognized band at all."""
    hit = _lookup_band(band)
    if not hit:
        return 1
    return hit[2] or 0


def file_safe_call(call):
    """A call sign usable in a file name: a rover's K2UA/R becomes K2UA-R."""
    return str(call).strip().upper().replace('/', '-')


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


# Other names a CSV header may use for each column, compared after
# _normalize_header (case, spaces and underscores ignored). The same names
# grid-mapper accepts, so one CSV works in both tools.
COLUMN_ALIASES = {
    'date': ('date', 'qsodate'),
    'band': ('band',),
    'sourcegrid': ('sourcegrid', 'mygrid', 'mygridsquare', 'homegrid', 'stationgrid'),
    'time': ('time', 'qsotime', 'timeon'),
    'call': ('call', 'callsign'),
    'grid': ('grid', 'gridsquare', 'theirgrid', 'dxgrid'),
}


class CsvHeaderError(ValueError):
    """A CSV header names some of the QSO columns but not all of them."""


def _match_columns(columns):
    """{raw column: header in the file} for every raw column the header names."""
    found = {}
    for header in columns:
        norm = _normalize_header(header)
        for col, aliases in COLUMN_ALIASES.items():
            if col not in found and norm in aliases:
                found[col] = header
                break
    return found


_CALL_RE = re.compile(r'^(?=.*\d)[A-Z0-9]{1,4}/?[A-Z0-9]{2,}(/[A-Z0-9]+)*$')
_GRID_RE = re.compile(r'^[A-R]{2}\d{2}([A-X]{2})?$')


def _first_rows_are_qsos(df, found):
    """Whether the two rows the legacy layout would skip hold contacts (a
    call sign or grid square where the header says one is). Only then is a
    half-recognized header a mistake worth stopping for; a sheet export's
    title and note rows aren't."""
    if len(df) < 2:
        return len(df) > 0
    checks = [(found[c], rx) for c, rx in (('call', _CALL_RE), ('grid', _GRID_RE)) if c in found]
    if not checks:
        return True
    return all(all(rx.match(str(df[col].iloc[i]).strip().upper()) for col, rx in checks)
               for i in range(2))


def load_local_raw_csv(path):
    """Load a local raw QSO CSV and normalize it to RAW_COLUMNS.

    Supports two layouts:
      - A header row naming date/band/sourcegrid/time/call/grid in any order
        and case (spaces/underscores ignored), or a common alternative name
        for each (see COLUMN_ALIASES, e.g. my_grid for sourcegrid) -- this is
        the format of the sample file in logs/ and the recommended format for
        a hand-exported CSV.
      - The legacy raw Google Sheets export layout: no usable header, two
        leading rows skipped, columns in fixed order. This matches a direct
        "File > Download > .csv" export of the original sheet template.

    A header that names some of the columns but not all raises CsvHeaderError
    instead: treating it as the legacy layout would silently drop the first
    two QSOs and read the columns in the wrong places.
    """
    df = pd.read_csv(path)
    found = _match_columns(df.columns)

    if len(found) == len(RAW_COLUMNS):
        renamed = df.rename(columns={header: col for col, header in found.items()})
        return renamed[RAW_COLUMNS].copy()
    if len(found) >= 2 and _first_rows_are_qsos(df, found):
        missing = [c for c in RAW_COLUMNS if c not in found]
        raise CsvHeaderError(
            f"{os.path.basename(str(path))}: the header row has no column for "
            f"{', '.join(missing)}. Name the columns {', '.join(RAW_COLUMNS)} "
            f"(for example, use 'sourcegrid' or 'my_grid' for your own grid).")

    # No recognizable header -- fall back to the legacy positional layout.
    contact_data = df.iloc[2:].copy()
    contact_data.columns = RAW_COLUMNS
    return contact_data


def parse_cabrillo_file(filename):
    """Parse a Cabrillo log file. Returns (DataFrame, callsign).

    QSO lines are read as: QSO: <band> <mode> <date> <time> <mycall> <mygrid>
    <call> <grid> -- the layout VHF and microwave contests use. The mode is
    kept in a 'mode' column alongside the six raw columns."""
    qsos = []
    callsign = None

    with open(filename, 'r', encoding='utf-8', errors='replace') as f:
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
                        'grid': parts[8] if len(parts) > 8 else '',
                        'mode': parts[2].upper(),
                    })
    return pd.DataFrame(qsos), callsign or "UNKNOWN"


ADIF_EXTENSIONS = ('.adi', '.adif')
_ADIF_FIELD_RE = re.compile(r'<([A-Za-z0-9_]+):(\d+)(?::[A-Za-z])?>', re.IGNORECASE)
_ADIF_END_RE = re.compile(r'<(eoh|eor)>', re.IGNORECASE)


def _adif_records(text):
    """Split ADIF text into (header dict, [record dicts]) with upper-case
    field names. Field values are read by their declared length, so values
    containing '<' or spaces come through intact."""
    header, records, current = {}, [], {}
    pos, in_header = 0, not text.lstrip().startswith('<')
    while True:
        match = _ADIF_FIELD_RE.search(text, pos)
        tag = _ADIF_END_RE.search(text, pos)
        if tag and (not match or tag.start() < match.start()):
            if tag.group(1).lower() == 'eoh':
                header.update(current)
                in_header = False
            elif current:
                records.append(current)
            current = {}
            pos = tag.end()
            continue
        if not match:
            break
        name, length = match.group(1).upper(), int(match.group(2))
        start = match.end()
        current[name] = text[start:start + length]
        pos = start + length
    if current and not in_header:
        records.append(current)
    return header, records


def parse_adif_file(filename):
    """Parse an ADIF (.adi) log. Returns (DataFrame, callsign).

    Uses CALL, GRIDSQUARE, MY_GRIDSQUARE, BAND (or FREQ), MODE, QSO_DATE and
    TIME_ON. QSL/confirmation fields are ignored -- to map only confirmed
    contacts, filter the ADIF file before uploading it. A record without its
    own MY_GRIDSQUARE gets the most common one in the file (many home-station
    logs only record it on some QSOs)."""
    with open(filename, 'r', encoding='utf-8', errors='replace') as f:
        _, records = _adif_records(f.read())

    my_grids = [r.get('MY_GRIDSQUARE', '').strip().upper() for r in records]
    known = [g for g in my_grids if g]
    fallback = max(set(known), key=known.count) if known else ''

    qsos, calls = [], []
    for record, my_grid in zip(records, my_grids):
        call = record.get('CALL', '').strip().upper()
        if not call:
            continue
        band = record.get('BAND', '').strip()
        if not band and record.get('FREQ'):
            band = band_from_freq_mhz(record['FREQ']) or record['FREQ']
        date = record.get('QSO_DATE', '').strip()
        if len(date) == 8 and date.isdigit():
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        qsos.append({
            'date': date,
            'band': band,
            'sourcegrid': my_grid or fallback,
            'time': record.get('TIME_ON', '').strip()[:4],
            'call': call,
            'grid': record.get('GRIDSQUARE', '').strip().upper(),
            # FT4, Q65 and friends are MODE MFSK with the real mode in SUBMODE.
            'mode': ((record.get('SUBMODE') if record.get('MODE', '').upper() == 'MFSK' else None)
                     or record.get('MODE', '')).strip().upper(),
        })
        station = (record.get('STATION_CALLSIGN') or record.get('OPERATOR') or '').strip().upper()
        if station:
            calls.append(station)

    callsign = max(set(calls), key=calls.count) if calls else "UNKNOWN"
    df = pd.DataFrame(qsos, columns=RAW_COLUMNS + ['mode'])
    return df, callsign


def is_adif_path(path):
    return str(path).lower().endswith(ADIF_EXTENSIONS)


def read_log_header(path):
    """Header facts a contest can be identified from, as a dict with
    upper-case Cabrillo tag names: CONTEST, CALLSIGN, CATEGORY-STATION,
    CATEGORY-OPERATOR, CATEGORY-BAND, CATEGORY-MODE, CATEGORY-POWER,
    HQ-CATEGORY, ... For an ADIF log, CONTEST comes from the most common
    CONTEST_ID. Missing or unreadable files give an empty dict."""
    header = {}
    try:
        if is_adif_path(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                _, records = _adif_records(f.read())
            ids = [r['CONTEST_ID'].strip().upper() for r in records if r.get('CONTEST_ID')]
            if ids:
                header['CONTEST'] = max(set(ids), key=ids.count)
            return header
        if not str(path).lower().endswith('.log'):
            return header
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('QSO:'):
                    break
                tag, sep, value = line.partition(':')
                if sep and tag.strip() and tag.strip().upper() not in header:
                    header[tag.strip().upper()] = value.strip()
    except OSError:
        pass
    return header


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
    elif is_adif_path(source):
        if verbose:
            print(f"Loading ADIF file: {source}")
        df, callsign = parse_adif_file(source)
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
    except (FileNotFoundError, CsvHeaderError) as e:
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
