#!/usr/bin/env python3
"""
Contest registry, automatic contest detection, and VHF contest scoring.

Each contest this project knows about is one Contest entry: its Cabrillo
name, when it runs, which bands count, and how a QSO scores. Scripts use
detect_contest() to work out which contest a log is from (the Cabrillo
CONTEST: header first, then the QSO dates), and score_vhf() to score the
ARRL January, June and September VHF contests, rovers included.

The ARRL 10 GHz and Up contest keeps its own distance-based scorer in
arrl_10ghz_cabrillo.py; it's registered here so detection can recognize it.

Rules: "January, June, and September VHF Contest Rules", ARRL, version 1.2.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from data_source import BAND_ORDER, TEN_GHZ_BANDS, normalize_band

ARRL_10_GHZ = 'ARRL-10-GHZ'
ARRL_VHF_JAN = 'ARRL-VHF-JAN'
ARRL_VHF_JUN = 'ARRL-VHF-JUN'
ARRL_VHF_SEP = 'ARRL-VHF-SEP'
VHF_CONTESTS = (ARRL_VHF_JAN, ARRL_VHF_JUN, ARRL_VHF_SEP)

# Bands the VHF contests count: 50 MHz and up (rule 2.1), i.e. every band in
# the band table.
VHF_BANDS = list(BAND_ORDER)
_LOW_POINT_BANDS = {'50 MHz': 1, '144 MHz': 1, '222 MHz': 2, '432 MHz': 2}


def _nth_saturday(year, month, n):
    """The n-th Saturday of a month (1-based). A weekend whose Saturday is
    the n-th Saturday is the n-th "full weekend" for every n a contest uses:
    the first Saturday of a month is at most the 7th, so its Sunday is
    always in the same month."""
    first = date(year, month, 1)
    return first + timedelta(days=(5 - first.weekday()) % 7 + 7 * (n - 1))


def _window(saturday, start_hour, end_hour_monday):
    start = datetime(saturday.year, saturday.month, saturday.day, start_hour)
    monday = saturday + timedelta(days=2)
    end = datetime(monday.year, monday.month, monday.day, end_hour_monday, 59)
    return start, end


@dataclass(frozen=True)
class Contest:
    contest_id: str           # Cabrillo CONTEST: name
    name: str                 # display name
    file_tag: str             # used in output file names
    month_weekends: tuple     # ((month, (n, ...)), ...) -- n-th full weekends
    start_hour: int           # Saturday start, UTC
    end_hour: int             # Monday end hour, UTC (ends at HH:59)
    bands: tuple              # bands that count

    def windows(self, year):
        """The contest's (start, end) UTC windows in a year. January VHF
        lists two candidate weekends (third or fourth, "as announced")."""
        result = []
        for month, weekends in self.month_weekends:
            for n in weekends:
                result.append(_window(_nth_saturday(year, month, n),
                                      self.start_hour, self.end_hour))
        return result

    def in_window(self, when):
        return any(start <= when <= end for start, end in self.windows(when.year))

    def qso_points(self, band):
        """QSO points for a band in a VHF contest (rules 5.1 and 5.2):
        January doubles the 902 MHz-and-up values."""
        band = normalize_band(band)
        if band in _LOW_POINT_BANDS:
            return _LOW_POINT_BANDS[band]
        january = self.contest_id == ARRL_VHF_JAN
        if band in ('902 MHz', '1.2 GHz'):
            return 4 if january else 3
        if band in VHF_BANDS:
            return 8 if january else 4
        return 0


CONTESTS = {
    # 10 GHz and Up: 6 AM Saturday to midnight Sunday local time, third full
    # weekends of August and September. The UTC window is wide enough for
    # every North American time zone.
    ARRL_10_GHZ: Contest(ARRL_10_GHZ, "ARRL 10 GHz and Up Contest", "ARRL_10GHZ",
                         ((8, (3,)), (9, (3,))), 8, 12,
                         tuple(TEN_GHZ_BANDS)),
    ARRL_VHF_JAN: Contest(ARRL_VHF_JAN, "ARRL January VHF Contest", "ARRL_VHF_JAN",
                          ((1, (3, 4)),), 19, 3, tuple(VHF_BANDS)),
    ARRL_VHF_JUN: Contest(ARRL_VHF_JUN, "ARRL June VHF Contest", "ARRL_VHF_JUN",
                          ((6, (2,)),), 18, 2, tuple(VHF_BANDS)),
    ARRL_VHF_SEP: Contest(ARRL_VHF_SEP, "ARRL September VHF Contest", "ARRL_VHF_SEP",
                          ((9, (2,)),), 18, 2, tuple(VHF_BANDS)),
}

# Other spellings seen in Cabrillo CONTEST: headers and ADIF CONTEST_ID.
_CONTEST_ALIASES = {
    'ARRL-10GHZ': ARRL_10_GHZ, 'ARRL-10-GHZ-UP': ARRL_10_GHZ, '10GHZ': ARRL_10_GHZ,
    'ARRL-VHF-JANUARY': ARRL_VHF_JAN, 'ARRL-JAN-VHF': ARRL_VHF_JAN,
    'ARRL-VHF-JUNE': ARRL_VHF_JUN, 'ARRL-JUN-VHF': ARRL_VHF_JUN,
    'ARRL-VHF-SEPTEMBER': ARRL_VHF_SEP, 'ARRL-SEP-VHF': ARRL_VHF_SEP,
}


def contest_from_name(name):
    """Contest id for a CONTEST: header / CONTEST_ID value, or None."""
    key = str(name or '').strip().upper().replace('_', '-').replace(' ', '-')
    if key in CONTESTS:
        return key
    return _CONTEST_ALIASES.get(key)


def parse_qso_datetime(date_value, time_value):
    """UTC datetime of a QSO from the log's date and HHMM time, or None."""
    text = str(date_value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%Y%m%d'):
        try:
            day = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    digits = re.sub(r'\D', '', str(time_value).split('.')[0]).zfill(4)[:4]
    try:
        return day.replace(hour=int(digits[:2]), minute=int(digits[2:]))
    except ValueError:
        return day


@dataclass
class Detection:
    contest_id: str      # None when no contest matches
    reason: str          # human-readable explanation

    @property
    def contest(self):
        return CONTESTS.get(self.contest_id)


def detect_contest(df, header=None):
    """Work out which contest a log is from.

    1. A recognized Cabrillo CONTEST: header (or ADIF CONTEST_ID) wins.
    2. Otherwise, the contest whose date window holds most of the QSOs.
    3. A date match is checked against the bands: a log dated in the
       September VHF weekend with only 10 GHz-and-up QSOs, or dated in a
       10 GHz weekend with no QSOs on a 10 GHz-and-up band, is no match.
    Returns a Detection; contest_id is None for "no contest"."""
    header = header or {}
    from_header = contest_from_name(header.get('CONTEST'))
    if from_header:
        return Detection(from_header, f"CONTEST: {header.get('CONTEST')} in the log header")

    times = [parse_qso_datetime(d, t) for d, t in zip(df['date'], df['time'])]
    times = [t for t in times if t is not None]
    if not times:
        return Detection(None, "no QSO dates could be read")

    bands = {normalize_band(b) for b in df['band']}
    counts = Counter()
    for contest in CONTESTS.values():
        counts[contest.contest_id] = sum(1 for t in times if contest.in_window(t))
    best, hits = counts.most_common(1)[0]
    if hits * 2 <= len(times):
        return Detection(None, "the QSO dates don't fall in any supported contest's weekend")

    contest = CONTESTS[best]
    if best == ARRL_10_GHZ and not bands & set(contest.bands):
        return Detection(None, "dated in a 10 GHz and Up weekend, but no QSOs on 10 GHz and up")
    if best in VHF_CONTESTS and bands <= set(CONTESTS[ARRL_10_GHZ].bands):
        return Detection(None, f"dated in the {contest.name} weekend, but every QSO "
                               "is on 10 GHz and up")
    return Detection(best, f"{hits} of {len(times)} QSOs fall in the {contest.name} weekend")


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

# Entry categories (rules 3; Entry Categories for VHF+ Contests). Each maps
# to the bands it scores (None = all) and whether it's a rover.
CATEGORIES = {
    'SO':  ("Single Operator", None, False),
    'SOP': ("Single Operator Portable", None, False),
    'SO3B': ("Single Operator, 3-Bands", ('50 MHz', '144 MHz', '432 MHz'), False),
    'SOFM': ("Single Operator, FM-Only",
             ('50 MHz', '144 MHz', '222 MHz', '432 MHz', '902 MHz', '1.2 GHz'), False),
    'R':   ("Classic Rover", None, True),
    'RL':  ("Limited Rover", ('50 MHz', '144 MHz', '222 MHz', '432 MHz'), True),
    'RU':  ("Unlimited Rover", None, True),
    'LM':  ("Limited Multioperator", None, False),
    'UM':  ("Unlimited Multioperator", None, False),
}
ANALOG_MODES = {'CW', 'PH', 'FM', 'SSB', 'USB', 'LSB', 'AM'}
FM_MODES = {'FM'}


def category_from_header(header, callsign=''):
    """Best-guess category code from a Cabrillo header (N1MM writes an
    HQ-CATEGORY line such as "Classic Rover"), else from the call sign."""
    hq = str(header.get('HQ-CATEGORY', '')).lower()
    station = str(header.get('CATEGORY-STATION', '')).upper()
    band = str(header.get('CATEGORY-BAND', '')).upper()
    operator = str(header.get('CATEGORY-OPERATOR', '')).upper()
    if 'limited rover' in hq or station == 'ROVER-LIMITED':
        return 'RL'
    if 'unlimited rover' in hq or station == 'ROVER-UNLIMITED':
        return 'RU'
    if 'rover' in hq or station.startswith('ROVER'):
        return 'R'
    if '3' in hq and 'band' in hq or band in ('3-BAND', '3BAND'):
        return 'SO3B'
    if 'fm' in hq or band == 'FM' or str(header.get('CATEGORY-MODE', '')).upper() == 'FM':
        return 'SOFM'
    if 'portable' in hq or station == 'PORTABLE':
        return 'SOP'
    if 'limited multi' in hq or (operator == 'MULTI-OP' and band == 'LIMITED'):
        return 'LM'
    if 'multi' in hq or operator == 'MULTI-OP':
        return 'UM'
    if not station and is_rover_call(callsign or header.get('CALLSIGN', '')):
        return 'R'
    return 'SO'


def is_rover_call(call):
    return str(call).strip().upper().endswith('/R')


def base_call(call):
    """Call sign without a /R (rover) suffix, for duplicate checking."""
    call = str(call).strip().upper()
    return call[:-2] if call.endswith('/R') else call


def is_analog_header(header):
    return 'analog' in str(header.get('HQ-CATEGORY', '')).lower() or \
        str(header.get('CATEGORY-MODE', '')).upper() == 'ANALOG'


# ---------------------------------------------------------------------------
# VHF scoring
# ---------------------------------------------------------------------------

def score_vhf(df, contest_id, category='SO', analog_only=False):
    """Score a log under the ARRL January/June/September VHF rules.

    - QSO points by band (rule 5.1 for January, 5.2 for June/September).
    - Duplicates: a station counts once per band from each grid pair (rule
      2.2 and VCAT.5.1.4) -- a rover can be worked again from a new grid,
      and a rover can work a station again after moving.
    - Multipliers: grid squares worked on each band (5.3).
    - Rovers add one multiplier per grid they made a contact from (5.4.2).
    - Category band limits (SO3B, SOFM, Limited Rover) and mode limits
      (SOFM: FM only; Analog Only: no digital modes). A QSO with no mode
      logged isn't dropped by a mode limit.
    - QSOs outside the contest weekend, below 50 MHz, without a 4-character
      grid, or with an aeronautical mobile (/AM) don't count.

    Returns a dict: total, qso_points, multipliers, band_grids, activated
    (sorted grids), bands {band: {qsos, points, grids}}, valid (list of
    per-QSO dicts in log order, with a running 'score'), dupes, excluded
    [(row index, reason)], warnings [str], rover (bool), category.
    """
    contest = CONTESTS[contest_id]
    name, band_limit, rover = CATEGORIES.get(category, CATEGORIES['SO'])
    has_mode = 'mode' in df.columns

    seen = set()
    band_grids = set()
    activated = set()
    qso_points = 0
    bands = {}
    valid, excluded, dupes = [], [], 0
    rover_pairs = Counter()

    for index, row in enumerate(df.itertuples(index=False)):
        band = normalize_band(row.band)
        call = str(row.call).strip().upper()
        my_grid = str(row.sourcegrid).strip().upper()[:4]
        grid = str(row.grid).strip().upper()[:4]
        mode = str(getattr(row, 'mode', '') or '').strip().upper() if has_mode else ''
        if mode == 'NAN':
            mode = ''
        when = parse_qso_datetime(row.date, row.time)

        reason = None
        if band not in contest.bands:
            reason = f"{row.band} isn't a contest band"
        elif band_limit and band not in band_limit:
            reason = f"{band} doesn't count for {name}"
        elif when is None:
            reason = "missing or unreadable date"
        elif not contest.in_window(when):
            reason = "outside the contest period"
        elif not re.match(r'^[A-R]{2}\d{2}$', grid) or not re.match(r'^[A-R]{2}\d{2}$', my_grid):
            reason = "missing or invalid grid square"
        elif call.endswith('/AM'):
            reason = "aeronautical mobile contacts don't count"
        elif category == 'SOFM' and mode and mode not in FM_MODES:
            reason = f"{mode or 'unknown'} mode doesn't count for {name}"
        elif analog_only and mode and mode not in ANALOG_MODES:
            reason = f"{mode or 'unknown'} mode doesn't count for Analog Only"
        if reason:
            excluded.append((index, reason))
            continue

        key = (band, my_grid, base_call(call), grid)
        if key in seen:
            dupes += 1
            excluded.append((index, "duplicate"))
            continue
        seen.add(key)

        points = contest.qso_points(band)
        qso_points += points
        band_grids.add((band, grid))
        if rover:
            activated.add(my_grid)
        if rover and is_rover_call(call):
            rover_pairs[base_call(call)] += 1
        stats = bands.setdefault(band, {'qsos': 0, 'points': 0, 'grids': set()})
        stats['qsos'] += 1
        stats['points'] += points
        stats['grids'].add(grid)
        multipliers = len(band_grids) + len(activated)
        valid.append({'index': index, 'band': band, 'call': call, 'my_grid': my_grid,
                      'grid': grid, 'points': points, 'score': qso_points * multipliers})

    warnings = []
    if not rover and len({v['my_grid'] for v in valid}) > 1:
        warnings.append("This log has QSOs from more than one grid square, but fixed "
                        "stations may not change locations (rule 6.3). Choose a rover "
                        "category if this was a rover entry.")
    for other, count in sorted(rover_pairs.items()):
        if count > 100 and category != 'RU':
            warnings.append(f"{count} QSOs with rover {other}/R: rovers may make no more "
                            "than 100 QSOs with any other specific rover (VCAT.5.3.6).")
    if category == 'LM' and len(bands) > 4:
        warnings.append("Limited Multioperator entries may include logs from up to "
                        f"four bands; this log has {len(bands)}.")

    multipliers = len(band_grids) + len(activated)
    ordered = {b: bands[b] for b in BAND_ORDER if b in bands}
    return {
        'contest': contest_id, 'category': category, 'rover': rover,
        'total': qso_points * multipliers, 'qso_points': qso_points,
        'multipliers': multipliers, 'band_grids': len(band_grids),
        'activated': sorted(activated), 'bands': ordered, 'valid': valid,
        'dupes': dupes, 'excluded': excluded, 'warnings': warnings,
    }
