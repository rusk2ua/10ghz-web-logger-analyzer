#!/usr/bin/env python3
"""
Score a log for the ARRL January, June or September VHF Contest, and write
a Cabrillo log plus a plain-text summary.

The contest is detected from the log (its CONTEST: header, else its QSO
dates) unless --contest is given. The entry category -- which decides the
rover scoring and any band or mode limits -- comes from --category, else
the log's header (N1MM's HQ-CATEGORY, CATEGORY-STATION), else a /R call
sign means Classic Rover.

    python vhf_cabrillo.py logs/my_june_log.log
    python vhf_cabrillo.py my_log.adi --contest JUN --category R
"""

import argparse
import sys
from collections import Counter, OrderedDict
from datetime import datetime

import contests
from contests import CATEGORIES, CONTESTS, VHF_CONTESTS
from data_source import (
    SOURCE_HELP, file_safe_call, load_source_or_exit, band_to_cabrillo, read_log_header,
)

VERSION = "1.7.1"

# Cabrillo QSO-line modes: CW, PH, FM, RY, DG.
_CABRILLO_MODES = {
    'CW': 'CW', 'PH': 'PH', 'SSB': 'PH', 'USB': 'PH', 'LSB': 'PH', 'AM': 'PH',
    'FM': 'FM', 'RY': 'RY', 'RTTY': 'RY', 'DG': 'DG',
}
# Header lines carried over from a Cabrillo source log.
_CARRIED_HEADER = ('LOCATION', 'CATEGORY-OPERATOR', 'CATEGORY-ASSISTED', 'CATEGORY-POWER',
                   'CATEGORY-BAND', 'CATEGORY-MODE', 'CATEGORY-TRANSMITTER',
                   'CATEGORY-STATION', 'GRID-LOCATOR', 'CLUB', 'OPERATORS', 'NAME')
# Defaults for a log that doesn't carry its own header.
_CATEGORY_HEADER = {
    'SO':   {'CATEGORY-OPERATOR': 'SINGLE-OP', 'CATEGORY-STATION': 'FIXED'},
    'SOP':  {'CATEGORY-OPERATOR': 'SINGLE-OP', 'CATEGORY-STATION': 'PORTABLE', 'CATEGORY-POWER': 'QRP'},
    'SO3B': {'CATEGORY-OPERATOR': 'SINGLE-OP', 'CATEGORY-STATION': 'FIXED', 'CATEGORY-BAND': '3-BAND'},
    'SOFM': {'CATEGORY-OPERATOR': 'SINGLE-OP', 'CATEGORY-STATION': 'FIXED', 'CATEGORY-BAND': 'FM',
             'CATEGORY-MODE': 'FM'},
    'R':    {'CATEGORY-OPERATOR': 'SINGLE-OP', 'CATEGORY-STATION': 'ROVER'},
    'RL':   {'CATEGORY-OPERATOR': 'SINGLE-OP', 'CATEGORY-STATION': 'ROVER-LIMITED'},
    'RU':   {'CATEGORY-OPERATOR': 'SINGLE-OP', 'CATEGORY-STATION': 'ROVER-UNLIMITED'},
    'LM':   {'CATEGORY-OPERATOR': 'MULTI-OP', 'CATEGORY-BAND': 'LIMITED'},
    'UM':   {'CATEGORY-OPERATOR': 'MULTI-OP', 'CATEGORY-BAND': 'ALL'},
}


def cabrillo_mode(mode, default='PH'):
    """Cabrillo QSO-line mode for a logged mode (FT8, Q65, MSK144, ... are
    DG). A QSO without a mode is written as `default`."""
    mode = str(mode or '').strip().upper()
    if not mode or mode == 'NAN':
        return default
    return _CABRILLO_MODES.get(mode, 'DG')


def contest_from_arg(value):
    """--contest value (JAN, JUN, SEP, a Cabrillo name, or AUTO) to an id."""
    value = str(value or '').strip().upper()
    if value in ('', 'AUTO'):
        return None
    for contest_id in VHF_CONTESTS:
        if value in (contest_id, contest_id[-3:]):
            return contest_id
    found = contests.contest_from_name(value)
    if found in VHF_CONTESTS:
        return found
    raise SystemExit(f"Unknown VHF contest '{value}' -- use JAN, JUN or SEP.")


def _qso_date(value):
    text = str(value)
    if '/' in text:
        try:
            return datetime.strptime(text, '%m/%d/%Y').strftime('%Y-%m-%d')
        except ValueError:
            pass
    return text


def generate_cabrillo(df, callsign, contest_id, category, header, score, analog_only=False):
    """Cabrillo log. Header lines from a Cabrillo source log are carried
    over, except that the lines naming the entry category always match the
    category being scored -- grid-mapper's contest movie reads them back to
    score the same way. Analog Only is written as X-ANALOG-ONLY: YES."""
    lines = ["START-OF-LOG: 3.0", f"CONTEST: {contest_id}", f"CALLSIGN: {callsign}"]
    fields = OrderedDict()
    for tag in _CARRIED_HEADER:
        if header.get(tag):
            fields[tag] = header[tag]
    fields.setdefault('CATEGORY-BAND', 'ALL')
    fields.setdefault('CATEGORY-MODE', 'MIXED')
    if category in ('SO3B', 'SOFM', 'LM', 'UM') or fields['CATEGORY-BAND'] in ('3-BAND', 'FM', 'LIMITED'):
        fields['CATEGORY-BAND'] = 'ALL'
    if category != 'SOFM' and fields['CATEGORY-MODE'] == 'FM':
        fields['CATEGORY-MODE'] = 'MIXED'
    fields.update(_CATEGORY_HEADER.get(category, {}))
    for tag, value in fields.items():
        lines.append(f"{tag}: {value}")
    if analog_only:
        lines.append("X-ANALOG-ONLY: YES")
    lines.append(f"CLAIMED-SCORE: {score}")
    lines.append(f"CREATED-BY: K2UA Python Logger v{VERSION}")
    has_mode = 'mode' in df.columns
    # A QSO without a mode (every QSO in a spreadsheet CSV) is written as PH
    # -- or FM for an FM-Only entry -- so it still counts when re-read.
    default_mode = 'FM' if category == 'SOFM' else 'PH'
    for row in df.itertuples(index=False):
        mode = cabrillo_mode(getattr(row, 'mode', '') if has_mode else '', default_mode)
        lines.append(
            f"QSO: {band_to_cabrillo(row.band)} {mode} {_qso_date(row.date)} "
            f"{str(row.time).zfill(4)[:4]} {callsign} {str(row.sourcegrid).upper()[:6]} "
            f"{str(row.call).upper()} {str(row.grid).upper()[:6]}")
    lines.append("END-OF-LOG:")
    return '\n'.join(lines) + '\n'


def rover_stops(df, result):
    """Per operating grid: first and last QSO time and how many QSOs counted,
    in the order the grids were first used."""
    stops = OrderedDict()
    for qso in result['valid']:
        row = df.iloc[qso['index']]
        when = f"{_qso_date(row['date'])} {str(row['time']).zfill(4)[:4]}"
        stop = stops.setdefault(qso['my_grid'], {'first': when, 'last': when, 'qsos': 0})
        stop['last'] = when
        stop['qsos'] += 1
    return stops


def generate_summary(df, callsign, contest_id, category, result, detection, analog_only):
    contest = CONTESTS[contest_id]
    years = Counter(str(_qso_date(d))[:4] for d in df['date'])
    year = years.most_common(1)[0][0] if years else ''
    label = CATEGORIES[category][0] + (" (Analog Only)" if analog_only else "")
    lines = [
        f"{contest.name}, {year}",
        f"Call\t\t{callsign}",
        f"Category\t{label}",
        f"Score\t\t{result['total']:,}",
        "",
        "Band\t\tQSOs\tQSO Pts\tGrids",
    ]
    for band, stats in result['bands'].items():
        lines.append(f"{band:<10}\t{stats['qsos']}\t{stats['points']}\t{len(stats['grids'])}")
    total_qsos = sum(s['qsos'] for s in result['bands'].values())
    lines.append(f"Total\t\t{total_qsos}\t{result['qso_points']}\t{result['band_grids']}")
    lines.append("")
    if result['rover']:
        lines.append(f"Grids activated: {len(result['activated'])} "
                     f"({', '.join(result['activated'])})")
        lines.append(f"Multipliers: {result['band_grids']} band-grids + "
                     f"{len(result['activated'])} grids activated = {result['multipliers']}")
    else:
        lines.append(f"Multipliers: {result['multipliers']} (grid squares worked per band)")
    lines.append(f"Score: {result['qso_points']:,} QSO points x {result['multipliers']} "
                 f"multipliers = {result['total']:,}")

    if result['rover']:
        lines += ["", "Rover stops", "Grid\tFirst QSO (UTC)\t\tLast QSO (UTC)\t\tQSOs"]
        for grid, stop in rover_stops(df, result).items():
            lines.append(f"{grid}\t{stop['first']}\t{stop['last']}\t{stop['qsos']}")

    lines += ["", f"Contest: {contest.name} ({detection})"]
    if result['excluded']:
        reasons = Counter(reason for _, reason in result['excluded'])
        lines += ["", f"Not counted: {len(result['excluded'])} QSO(s)"]
        for reason, count in reasons.most_common():
            lines.append(f"  {count} x {reason}")
    for warning in result['warnings']:
        lines += ["", f"Warning: {warning}"]
    return '\n'.join(lines) + '\n'


def build_parser():
    parser = argparse.ArgumentParser(
        description="Score a log for the ARRL January, June or September VHF Contest "
                    "and write a Cabrillo log and summary.")
    parser.add_argument('source', nargs='?', default=None, help=SOURCE_HELP)
    parser.add_argument('--contest', default='AUTO',
                        help="JAN, JUN, SEP, or AUTO (default) to detect it from the log")
    parser.add_argument('--category', default='AUTO', choices=['AUTO', *CATEGORIES],
                        help="Entry category (default: from the log header, or R for a /R call)")
    parser.add_argument('--analog', action='store_true',
                        help="Analog Only subcategory: digital-mode QSOs don't count")
    return parser


def main():
    args = build_parser().parse_args()
    df, callsign = load_source_or_exit(args.source)
    header = read_log_header(args.source) if args.source else {}

    if callsign == "UNKNOWN":
        callsign = input("Your callsign (e.g., W1ABC): ").strip().upper() or "UNKNOWN"

    contest_id = contest_from_arg(args.contest)
    if contest_id:
        detection = "chosen"
    else:
        found = contests.detect_contest(df, header)
        if found.contest_id not in VHF_CONTESTS:
            print(f"Couldn't tell which VHF contest this log is from ({found.reason}).")
            answer = input("Contest (JAN, JUN or SEP): ").strip()
            contest_id = contest_from_arg(answer)
            if not contest_id:
                print("Error: no contest chosen -- use --contest JAN, JUN or SEP.")
                sys.exit(1)
            detection = "chosen"
        else:
            contest_id, detection = found.contest_id, f"detected: {found.reason}"

    category = (contests.category_from_header(header, callsign)
                if args.category == 'AUTO' else args.category)
    analog_only = args.analog or contests.is_analog_header(header)

    result = contests.score_vhf(df, contest_id, category, analog_only)
    contest = CONTESTS[contest_id]
    print(f"{contest.name}: {CATEGORIES[category][0]}")
    print(f"QSO points {result['qso_points']} x multipliers {result['multipliers']} "
          f"= {result['total']}")
    for warning in result['warnings']:
        print(f"Warning: {warning}")

    stem = f"{file_safe_call(callsign)}_{contest.file_tag}"
    with open(f"{stem}.log", 'w', encoding='utf-8') as f:
        f.write(generate_cabrillo(df, callsign, contest_id, category, header, result['total'],
                                  analog_only))
    with open(f"{stem}_Summary.txt", 'w', encoding='utf-8') as f:
        f.write(generate_summary(df, callsign, contest_id, category, result, detection,
                                 analog_only))
    print(f"\nCabrillo file saved as: {stem}.log")
    print(f"Summary file saved as: {stem}_Summary.txt")
    print(f"Total QSOs: {len(df)}")


if __name__ == "__main__":
    main()
