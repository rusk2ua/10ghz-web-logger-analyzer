#!/usr/bin/env python3
import os
import re
import csv
import math
import json
import html as html_module
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.patheffects as path_effects
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cartopy.io.img_tiles as cimgt
from shapely.geometry import box as shapely_box, mapping as shapely_mapping
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# Minimum frequency, in MHz, for each band label this script produces.
# Used to decide whether a band qualifies for the --osm-basemap underlay
# (902 MHz and up, i.e. 33cm and shorter wavelengths).
BAND_MIN_FREQ_MHZ = {
    '630m': 0.472, '160m': 1.8, '80m': 3.5, '60m': 5.33, '40m': 7.0, '30m': 10.1,
    '20m': 14.0, '17m': 18.068, '15m': 21.0, '12m': 24.89, '10m': 28.0,
    '6m': 50.0, '2m': 144.0, '1.25m': 222.0,
    '70cm': 420.0, '33cm': 902.0, '23cm': 1240.0,
    '13cm': 2300.0, '9cm': 3300.0, '6cm': 5650.0, '3cm': 10000.0,
    '1.25cm': 24000.0, '6mm': 47000.0, '4mm': 75500.0, '2.5mm': 119980.0,
    '2mm': 134000.0, '1mm': 241000.0,
    '10G': 10000.0, '24G': 24000.0, '47G': 47000.0, '75G': 75000.0, '123G': 123000.0,
    '134G': 134000.0, '142G': 142000.0, '241G': 241000.0, '300G': 300000.0,
}

OSM_MIN_FREQ_MHZ = 902.0  # 902 MHz and up, per the --osm-basemap band cutoff

def band_min_freq_mhz(band):
    """Best-effort lower-edge frequency of a band label, in MHz, or None if the
    label can't be identified. Known band names (see BAND_MIN_FREQ_MHZ) are
    looked up directly. Anything else is parsed generically so new or unusual
    labels work without a table entry:

    - a number with a frequency unit: "915MHz", "3.4GHz", "5.7G", "134G"
      (a bare "G" is how Cabrillo writes GHz bands: 1.2G, 10G, 24G, 134G, ...)
    - a bare number, taken as MHz, the Cabrillo convention: "144", "432"
    - a wavelength: "23cm", "4mm", "40m"
    """
    if band in BAND_MIN_FREQ_MHZ:
        return BAND_MIN_FREQ_MHZ[band]

    label = band.strip()
    match = re.match(r'^([\d.]+)\s*(GHz|MHz|kHz|G)?$', label, re.IGNORECASE)
    if match:
        try:
            value = float(match.group(1))
        except ValueError:
            return None
        unit = (match.group(2) or 'MHz').lower()
        if unit in ('ghz', 'g'):
            return value * 1000
        if unit == 'khz':
            return value / 1000
        return value

    match = re.match(r'^([\d.]+)\s*(cm|mm|m)$', label, re.IGNORECASE)
    if match:
        try:
            value = float(match.group(1))
        except ValueError:
            return None
        if value <= 0:
            return None
        metres = value * {'m': 1.0, 'cm': 0.01, 'mm': 0.001}[match.group(2).lower()]
        return 299.792458 / metres  # c / wavelength, in MHz
    return None


def band_is_osm_eligible(band):
    """Whether a band label represents 902 MHz or higher, i.e. whether the
    --osm-basemap street underlay applies. Works for any band label that
    band_min_freq_mhz() can identify, including Cabrillo GHz codes such as
    "134G". Returns False (conservatively, no basemap) if the band can't be
    identified.
    """
    freq = band_min_freq_mhz(band)
    return freq is not None and freq >= OSM_MIN_FREQ_MHZ


def band_has_grid_outlines(band):
    """Whether maps for this band draw the 1x2 degree grid-square outlines:
    6 m (50 MHz) and up. Unidentified labels containing "GHz" still count."""
    freq = band_min_freq_mhz(band)
    if freq is not None:
        return freq >= 50.0
    return 'GHz' in band


def estimate_osm_zoom(lon_min, lon_max, lat_min, lat_max):
    """Pick a reasonable OpenStreetMap tile zoom level for the given extent.
    Capped at 11 to keep the number of tiles fetched from the public OSM
    tile server modest, regardless of how tight the view is."""
    span = max(lon_max - lon_min, 0.01)
    zoom_table = [(40, 4), (20, 5), (10, 6), (5, 7), (2.5, 8), (1.2, 9), (0.5, 10)]
    for threshold, zoom in zoom_table:
        if span >= threshold:
            return zoom
    return 11

def add_osm_underlay(ax, lon_min, lon_max, lat_min, lat_max, opacity=0.3):
    """Add a faint OpenStreetMap tile basemap beneath the rest of the map's
    layers, plus the attribution OSM's tile usage policy requires. Fails
    quietly (map generation continues without the basemap) if the tiles
    can't be fetched, e.g. no internet connection."""
    try:
        tiler = cimgt.OSM(user_agent='grid-mapper (https://github.com/rusk2ua/grid-mapper)')
        zoom = estimate_osm_zoom(lon_min, lon_max, lat_min, lat_max)
        ax.add_image(tiler, zoom, alpha=opacity)
        ax.text(0.995, 0.005, '© OpenStreetMap contributors', transform=ax.transAxes,
                fontsize=6, ha='right', va='bottom', color='black', zorder=100,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7, edgecolor='none'))
    except Exception as e:
        print(f"Warning: could not load OpenStreetMap basemap ({e}); continuing without it")

# Continent boundaries (approximate)
CONTINENT_BOUNDS = {
    'north_america': {'lat': (10, 85), 'lon': (-180, -30)},
    'south_america': {'lat': (-60, 15), 'lon': (-90, -30)},
    'europe': {'lat': (35, 75), 'lon': (-15, 45)},
    'africa': {'lat': (-40, 40), 'lon': (-20, 55)},
    'asia': {'lat': (5, 80), 'lon': (25, 180)},
    'oceania': {'lat': (-50, 0), 'lon': (110, 180)}
}

def maidenhead_to_bounds(grid):
    """Convert Maidenhead grid square to lat/lon bounds"""
    grid = grid.upper().strip()
    
    lon_base = (ord(grid[0]) - ord('A')) * 20 - 180
    lat_base = (ord(grid[1]) - ord('A')) * 10 - 90
    
    if len(grid) >= 4:
        lon_base += int(grid[2]) * 2
        lat_base += int(grid[3]) * 1
        
        if len(grid) == 4:
            return lat_base, lat_base + 1, lon_base, lon_base + 2
        elif len(grid) == 6:
            lon_base += (ord(grid[4]) - ord('A')) * (2/24)
            lat_base += (ord(grid[5]) - ord('A')) * (1/24)
            return lat_base, lat_base + (1/24), lon_base, lon_base + (2/24)

    return None

def grid_center(grid):
    """Return the (lat, lon) center point of a Maidenhead grid square, using
    whatever precision the grid string provides (4- or 6-character)."""
    bounds = maidenhead_to_bounds(grid)
    if not bounds:
        return None
    lat_min, lat_max, lon_min, lon_max = bounds
    return (lat_min + lat_max) / 2, (lon_min + lon_max) / 2

EARTH_RADIUS_KM = 6371.0088
EARTH_RADIUS_MILES = 3958.7613

def haversine_distance(lat1, lon1, lat2, lon2, radius=EARTH_RADIUS_KM):
    """Great-circle distance between two lat/lon points, in whatever unit
    `radius` is expressed in (defaults to kilometers)."""
    lat1_r, lon1_r, lat2_r, lon2_r = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(min(1, math.sqrt(a)))
    return radius * c

def grid_distance_km(grid1, grid2):
    """Great-circle distance in kilometers between the centers of two
    Maidenhead grid squares, or None if either grid is invalid. Kilometers
    are used because ARRL 10 GHz and Up Contest (and similar) scoring is
    distance-based in km."""
    center1 = grid_center(grid1)
    center2 = grid_center(grid2)
    if not center1 or not center2:
        return None
    return haversine_distance(center1[0], center1[1], center2[0], center2[1], EARTH_RADIUS_KM)

def grid_distance_miles(grid1, grid2):
    """Great-circle distance in statute miles between the centers of two
    Maidenhead grid squares, or None if either grid is invalid."""
    center1 = grid_center(grid1)
    center2 = grid_center(grid2)
    if not center1 or not center2:
        return None
    return haversine_distance(center1[0], center1[1], center2[0], center2[1], EARTH_RADIUS_MILES)

def get_grid_continent(grid):
    """Determine which continent a grid square belongs to"""
    bounds = maidenhead_to_bounds(grid)
    if not bounds:
        return None
    
    lat_min, lat_max, lon_min, lon_max = bounds
    lat_center = (lat_min + lat_max) / 2
    lon_center = (lon_min + lon_max) / 2
    
    for continent, bounds in CONTINENT_BOUNDS.items():
        if (bounds['lat'][0] <= lat_center <= bounds['lat'][1] and 
            bounds['lon'][0] <= lon_center <= bounds['lon'][1]):
            return continent
    
    return 'other'

def parse_csv_grids(filename):
    """Extract Maidenhead grid squares by band from CSV format file.

    Returns (grids_by_band, callsign, home_grid, contacts_by_band,
    qso_records_by_band). home_grid comes from a "my grid" style column if
    the CSV has one; otherwise None (the caller can fall back to a
    --home-grid CLI override).
    contacts_by_band maps band -> list of (their_callsign, their_grid,
    qso_home_grid) tuples, used for the --paths map; callsign is "Unknown" if
    no callsign column could be identified. qso_home_grid is that row's own
    "my grid" value (or None), letting a log with multiple operating
    locations be split per location.
    qso_records_by_band maps band -> list of per-contact detail dicts, in the
    same order as contacts_by_band[band], used for the --html interactive
    popups. CSV files rarely carry as much detail as Cabrillo logs, so
    fields that have no matching column are simply omitted (None / empty).
    """
    grids_by_band = defaultdict(list)
    contacts_by_band = defaultdict(list)
    qso_records_by_band = defaultdict(list)
    callsign = "Unknown"
    home_grid_candidates = Counter()

    try:
        with open(filename, 'r') as f:
            # Extract callsign from first line if it contains one
            first_line = f.readline().strip()
            # Look for callsign pattern in first line (letters followed by numbers)
            import re
            callsign_match = re.search(r'\b([A-Z]{1,2}[0-9][A-Z]{1,3})\b', first_line)
            if callsign_match:
                callsign = callsign_match.group(1)

            # Find the actual header row by looking for multiple field names together
            f.seek(0)
            lines = f.readlines()
            header_row_idx = 0

            for i, line in enumerate(lines):
                line_lower = line.lower()
                # Look for multiple header fields in the same line
                field_count = sum(1 for field in ['date', 'time', 'call', 'grid', 'freq', 'band']
                                if field in line_lower)
                if field_count >= 3:  # Need at least 3 fields to be a header row
                    header_row_idx = i
                    break

            # Read from the header row
            f.seek(0)
            for _ in range(header_row_idx):
                f.readline()

            reader = csv.DictReader(f)
            headers = [h.lower().strip() for h in reader.fieldnames] if reader.fieldnames else []

            # Common field mappings - use original case for keys
            original_headers = reader.fieldnames if reader.fieldnames else []
            freq_fields = ['freq', 'frequency', 'band', 'freq_mhz']
            grid_fields = ['grid', 'gridsquare', 'grid_square', 'their_grid', 'dx_grid']
            call_fields = ['call', 'callsign', 'station_callsign', 'my_call']
            home_grid_fields = ['my_grid', 'mygrid', 'my_gridsquare', 'home_grid', 'station_grid', 'source_grid']
            date_fields = ['date', 'qso_date']
            time_fields = ['time', 'qso_time']

            # Find fields by matching lowercase versions
            freq_field = None
            grid_field = None
            call_field = None
            home_grid_field = None
            date_field = None
            time_field = None

            for orig, lower in zip(original_headers, headers):
                if not freq_field and lower in freq_fields:
                    freq_field = orig
                if not grid_field and lower in grid_fields:
                    grid_field = orig
                if not call_field and lower in call_fields:
                    call_field = orig
                if not home_grid_field and lower in home_grid_fields:
                    home_grid_field = orig
                if not date_field and lower in date_fields:
                    date_field = orig
                if not time_field and lower in time_fields:
                    time_field = orig

            for row in reader:
                # Extract frequency/band
                band = "Unknown"
                if freq_field:
                    freq_val = row[freq_field].strip()
                    if freq_val.isdigit():
                        band = freq_to_band(freq_val)
                    else:
                        band = freq_val

                # Extract the logging station's own grid for this row, if the CSV has that column
                qso_home_grid = None
                if home_grid_field:
                    home_grid_val = row[home_grid_field].strip().upper()
                    if is_valid_grid(home_grid_val):
                        qso_home_grid = home_grid_val
                        home_grid_candidates[home_grid_val] += 1

                # Extract grid square
                if grid_field:
                    grid = row[grid_field].strip().upper()
                    if is_valid_grid(grid):
                        grids_by_band[band].append(grid)
                        their_call = row[call_field].strip().upper() if call_field else "Unknown"
                        contacts_by_band[band].append((their_call or "Unknown", grid, qso_home_grid))
                        qso_records_by_band[band].append({
                            'call': their_call or "Unknown",
                            'grid': grid,
                            'home_grid': qso_home_grid,
                            'date': row[date_field].strip() if date_field else None,
                            'time': row[time_field].strip() if time_field else None,
                            'freq': row[freq_field].strip() if freq_field else None,
                            'mode': None,
                            'extra': [],
                        })

                # Also check all fields for grid patterns if no specific grid field
                if not grid_field:
                    for value in row.values():
                        if value and is_valid_grid(value.strip()):
                            grids_by_band[band].append(value.strip().upper())

    except Exception as e:
        print(f"Error parsing CSV file: {e}")
        return {}, callsign, None, {}, {}

    home_grid = home_grid_candidates.most_common(1)[0][0] if home_grid_candidates else None

    return dict(grids_by_band), callsign, home_grid, dict(contacts_by_band), dict(qso_records_by_band)

def parse_cabrillo_grids(filename):
    """Extract Maidenhead grid squares by band from Cabrillo format file.

    Returns (grids_by_band, callsign, home_grid, contacts_by_band,
    qso_records_by_band). home_grid is the logging station's overall (most
    common) grid square, auto-detected from the QSO exchange fields
    (Cabrillo QSO lines are: QSO: freq mode date time mycall myexch theircall
    theirexch). It is excluded from grids_by_band so it isn't double-counted
    as if it were a worked station.
    contacts_by_band maps band -> list of (their_callsign, their_grid,
    qso_home_grid) tuples, used for the --paths map. qso_home_grid is the
    operator's own grid as reported on that specific QSO line (or None if not
    present on that line), which lets a rover log with multiple operating
    locations be split per location.
    qso_records_by_band maps band -> list of per-contact detail dicts, in the
    same order as contacts_by_band[band] (one dict per worked-station grid),
    used to build the --html interactive popups: call, grid, home_grid, date,
    time, freq (raw), mode, and extra (any exchange tokens that weren't
    consumed as a callsign or grid, e.g. a serial number some contest
    exchanges include).
    """
    grids_by_band = defaultdict(list)
    contacts_by_band = defaultdict(list)
    qso_records_by_band = defaultdict(list)
    callsign = "Unknown"
    home_grid_candidates = Counter()

    try:
        with open(filename, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('CALLSIGN:'):
                    callsign = line.split(':', 1)[1].strip()
                elif line.startswith('QSO:'):
                    parts = line.split()
                    if len(parts) >= 6:
                        freq = parts[1]
                        mode = parts[2]
                        date = parts[3]
                        qso_time = parts[4]
                        band = freq_to_band(freq)

                        # Fields after "QSO: freq mode date time mycall" are the
                        # exchange: normally [myexch, theircall, theirexch, ...].
                        exchange_fields = [p.upper() for p in parts[6:]]

                        # If the first exchange field is itself a valid grid, it's
                        # this station's own (home) grid, not a worked contact.
                        remaining_fields = exchange_fields
                        qso_home_grid = None
                        if exchange_fields and is_valid_grid(exchange_fields[0]):
                            qso_home_grid = exchange_fields[0]
                            home_grid_candidates[qso_home_grid] += 1
                            remaining_fields = exchange_fields[1:]

                        # Pair each worked-station grid with the callsign token
                        # that immediately precedes it in the exchange. Any
                        # token that gets overwritten as "last_call" before
                        # ever being used (e.g. a serial number ahead of the
                        # real callsign) is kept as an "extra" detail instead
                        # of silently discarded.
                        last_call = None
                        pending_extra = []
                        for field in remaining_fields:
                            if is_valid_grid(field):
                                call = last_call or "Unknown"
                                grids_by_band[band].append(field)
                                contacts_by_band[band].append((call, field, qso_home_grid))
                                qso_records_by_band[band].append({
                                    'call': call,
                                    'grid': field,
                                    'home_grid': qso_home_grid,
                                    'date': date,
                                    'time': qso_time,
                                    'freq': freq,
                                    'mode': mode,
                                    'extra': list(pending_extra),
                                })
                                last_call = None
                                pending_extra = []
                            else:
                                if last_call is not None:
                                    pending_extra.append(last_call)
                                last_call = field
    except FileNotFoundError:
        print(f"File {filename} not found")
        return {}, callsign, None, {}, {}

    home_grid = home_grid_candidates.most_common(1)[0][0] if home_grid_candidates else None

    return dict(grids_by_band), callsign, home_grid, dict(contacts_by_band), dict(qso_records_by_band)

def is_valid_grid(grid):
    """Check if string is a valid Maidenhead grid square"""
    if not grid or len(grid) not in [4, 6]:
        return False
    
    grid = grid.upper()
    if len(grid) == 4:
        return (grid[0] in 'ABCDEFGHIJKLMNOPQR' and 
                grid[1] in 'ABCDEFGHIJKLMNOPQR' and 
                grid[2] in '0123456789' and 
                grid[3] in '0123456789')
    elif len(grid) == 6:
        return (grid[0] in 'ABCDEFGHIJKLMNOPQR' and 
                grid[1] in 'ABCDEFGHIJKLMNOPQR' and 
                grid[2] in '0123456789' and 
                grid[3] in '0123456789' and
                grid[4] in 'ABCDEFGHIJKLMNOPQRSTUVWX' and
                grid[5] in 'ABCDEFGHIJKLMNOPQRSTUVWX')
    return False

# Some logging software (notably for the ARRL 10 GHz and Up Contest) writes the
# QSO frequency field as a bare whole-GHz number instead of a kHz frequency
# (e.g. "10" instead of "10368000"). Recognize those directly so they map to
# the same band labels already used elsewhere in this project (see samples/).
GHZ_SHORTHAND_BANDS = {
    10: '10G',
    24: '24G',
    47: '47G',
    75: '75G',
    123: '123G',
}

def freq_to_band(freq_str):
    """Convert frequency string to band name using Cabrillo standard nomenclature"""
    try:
        freq = int(freq_str)

        if freq in GHZ_SHORTHAND_BANDS:
            return GHZ_SHORTHAND_BANDS[freq]

        # LF/MF bands
        if 472 <= freq <= 479:
            return "630m"
        elif 1800 <= freq <= 2000:
            return "160m"
        elif 3500 <= freq <= 4000:
            return "80m"
        elif 5330 <= freq <= 5405:
            return "60m"
        elif 7000 <= freq <= 7300:
            return "40m"
        elif 10100 <= freq <= 10150:
            return "30m"
        elif 14000 <= freq <= 14350:
            return "20m"
        elif 18068 <= freq <= 18168:
            return "17m"
        elif 21000 <= freq <= 21450:
            return "15m"
        elif 24890 <= freq <= 24990:
            return "12m"
        elif 28000 <= freq <= 29700:
            return "10m"
        
        # VHF bands
        elif freq == 50 or (50000 <= freq <= 54000):
            return "6m"
        elif freq == 144 or (144000 <= freq <= 148000):
            return "2m"
        elif freq == 222 or (222000 <= freq <= 225000):
            return "1.25m"
        
        # UHF bands
        elif freq == 432 or (420000 <= freq <= 450000):
            return "70cm"
        elif freq in [902, 903] or (902000 <= freq <= 928000):
            return "33cm"
        elif freq == 1296 or (1240000 <= freq <= 1300000):
            return "23cm"
        
        # Microwave bands
        elif 2300000 <= freq <= 2450000:
            return "13cm"
        elif 3300000 <= freq <= 3500000:
            return "9cm"
        elif 5650000 <= freq <= 5925000:
            return "6cm"
        elif 10000000 <= freq <= 10500000:
            return "3cm"
        elif 24000000 <= freq <= 24250000:
            return "1.25cm"
        elif 47000000 <= freq <= 47200000:
            return "6mm"
        elif 75500000 <= freq <= 81000000:
            return "4mm"
        elif 119980000 <= freq <= 120020000:
            return "2.5mm"
        elif 134000000 <= freq <= 149000000:
            return "2mm"
        elif 241000000 <= freq <= 250000000:
            return "1mm"
        elif 275000000 <= freq <= 1000000000:
            return "300G"
        
        # Handle frequency in MHz format (common in logs)
        elif freq < 1000:
            if freq == 472:
                return "630m"
            elif freq == 1800 or freq == 1900:
                return "160m"
            elif freq == 3500 or freq == 3700 or freq == 3800:
                return "80m"
            elif freq == 5300:
                return "60m"
            elif freq == 7000 or freq == 7100 or freq == 7200:
                return "40m"
            elif freq == 10100:
                return "30m"
            elif freq == 14000 or freq == 14100 or freq == 14200:
                return "20m"
            elif freq == 18100:
                return "17m"
            elif freq == 21000 or freq == 21100 or freq == 21200:
                return "15m"
            elif freq == 24900:
                return "12m"
            elif freq == 28000 or freq == 28100 or freq == 28200:
                return "10m"
            elif freq == 50:
                return "6m"
            elif freq == 144:
                return "2m"
            elif freq == 222:
                return "1.25m"
            elif freq == 432:
                return "70cm"
            elif freq in [902, 903]:
                return "33cm"
            else:
                return f"{freq}MHz"
        else:
            return f"{freq}kHz"
            
    except ValueError:
        # Handle non-numeric frequency strings
        freq_str = freq_str.upper().strip()
        
        # Direct band name mappings
        band_mappings = {
            '630M': '630m', '160M': '160m', '80M': '80m', '60M': '60m',
            '40M': '40m', '30M': '30m', '20M': '20m', '17M': '17m',
            '15M': '15m', '12M': '12m', '10M': '10m', '6M': '6m',
            '2M': '2m', '1.25M': '1.25m', '70CM': '70cm', '33CM': '33cm',
            '23CM': '23cm', '13CM': '13cm', '9CM': '9cm', '6CM': '6cm',
            '3CM': '3cm', '1.25CM': '1.25cm', '6MM': '6mm', '4MM': '4mm',
            '2.5MM': '2.5mm', '2MM': '2mm', '1MM': '1mm'
        }
        
        return band_mappings.get(freq_str, freq_str)

def auto_select_continents(grids):
    """Automatically determine which continents to include based on grid squares"""
    continents = set()
    for grid in grids:
        continent = get_grid_continent(grid)
        if continent:
            continents.add(continent)
    return list(continents)

def get_optimal_bounds(grids):
    """Calculate optimal map bounds based on actual grid square locations"""
    if not grids:
        return (-180, 180, -90, 90)
    
    lats = []
    lons = []
    
    for grid in grids:
        bounds = maidenhead_to_bounds(grid)
        if bounds:
            lat_min, lat_max, lon_min, lon_max = bounds
            lats.extend([lat_min, lat_max])
            lons.extend([lon_min, lon_max])
    
    if not lats or not lons:
        return (-180, 180, -90, 90)
    
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    
    # Add padding based on the span
    lat_span = max_lat - min_lat
    lon_span = max_lon - min_lon
    
    # Use smaller padding for regional views
    lat_padding = max(lat_span * 0.15, 2.0)  # At least 2 degrees
    lon_padding = max(lon_span * 0.15, 3.0)  # At least 3 degrees
    
    return (min_lon - lon_padding, max_lon + lon_padding,
            min_lat - lat_padding, max_lat + lat_padding)

def get_region_name(lon_min, lon_max, lat_min, lat_max):
    """Generate a descriptive region name based on bounds"""
    # North America regions
    if -170 <= lon_min and lon_max <= -30 and 10 <= lat_min and lat_max <= 85:
        if lon_min >= -100 and lat_min >= 35:
            return "northeastern_north_america"
        elif lon_min >= -100 and lat_max <= 45:
            return "southeastern_north_america"
        elif lon_max <= -95 and lat_min >= 35:
            return "northwestern_north_america"
        elif lon_max <= -95 and lat_max <= 45:
            return "southwestern_north_america"
        elif lat_min >= 45:
            return "northern_north_america"
        elif lat_max <= 35:
            return "southern_north_america"
        elif lon_min >= -100:
            return "eastern_north_america"
        elif lon_max <= -95:
            return "western_north_america"
        else:
            return "central_north_america"
    
    # Europe regions
    elif -15 <= lon_min and lon_max <= 45 and 35 <= lat_min and lat_max <= 75:
        if lat_min >= 55:
            return "northern_europe"
        elif lat_max <= 50:
            return "southern_europe"
        elif lon_min >= 15:
            return "eastern_europe"
        elif lon_max <= 5:
            return "western_europe"
        else:
            return "central_europe"
    
    # Use continent names for larger areas
    continents = auto_select_continents(list(grid_counts.keys()) if 'grid_counts' in locals() else [])
    if len(continents) == 1:
        return continents[0]
    elif continents:
        return '_'.join(continents)
    else:
        return "regional"

def filter_grids_by_continents(grids, continents):
    """Filter grid squares to only include those in specified continents"""
    if not continents:
        return grids
    
    filtered = {}
    for grid, count in grids.items():
        grid_continent = get_grid_continent(grid)
        if grid_continent in continents:
            filtered[grid] = count
    
    return filtered

def _esc(text):
    """HTML-escape a value for safe embedding in generated popup markup."""
    return html_module.escape(str(text)) if text is not None else ''

def _format_qso_detail_lines(rec):
    """Build the 'date/time' and 'other details' display strings for one QSO
    detail record (see parse_cabrillo_grids/parse_csv_grids)."""
    date_time_bits = []
    if rec.get('date'):
        date_time_bits.append(str(rec['date']))
    t = rec.get('time')
    if t:
        t = str(t)
        date_time_bits.append(f"{t[:2]}:{t[2:]}Z" if len(t) == 4 else t)
    date_line = ' '.join(date_time_bits)

    detail_bits = []
    if rec.get('mode'):
        detail_bits.append(str(rec['mode']))
    if rec.get('freq'):
        detail_bits.append(str(rec['freq']))
    detail_line = '  '.join(detail_bits)

    return date_line, detail_line, (rec.get('extra') or [])

def _group_qso_records_for_popup(records):
    """Group QSO records sharing a grid square / worked-station dot by
    callsign. Repeat contacts with the SAME station collapse to just the
    most recent one, annotated with a "(xN)" count; different callsigns
    worked from the same grid each get their own listed entry."""
    by_call = defaultdict(list)
    order = []
    for rec in records:
        call = rec.get('call') or "Unknown"
        if call not in by_call:
            order.append(call)
        by_call[call].append(rec)

    groups = []
    for call in order:
        recs_sorted = sorted(by_call[call], key=lambda r: (r.get('date') or '', r.get('time') or ''))
        groups.append((call, len(recs_sorted), recs_sorted[-1]))
    return groups

def _build_popup_html(grid, records):
    """Build the interactive popup content for one grid square / worked-
    station dot: callsign in larger type, then date, then the six-character
    grid, then whatever other QSO details are available (mode, frequency,
    any leftover exchange tokens). Multiple contacts to the same grid are
    listed; repeat contacts with the same station collapse to the most
    recent one with a "(xN)" count."""
    if not records:
        return (f'<div class="qso-popup"><div class="qso-grid">{_esc(grid)}</div>'
                f'<div class="qso-detail">No contact details available</div></div>')

    parts = ['<div class="qso-popup">']
    for call, count, rec in _group_qso_records_for_popup(records):
        call_label = call if count <= 1 else f"{call} (×{count})"
        date_line, detail_line, extra = _format_qso_detail_lines(rec)
        parts.append('<div class="qso-entry">')
        parts.append(f'<div class="qso-call">{_esc(call_label)}</div>')
        if date_line:
            parts.append(f'<div class="qso-date">{_esc(date_line)}</div>')
        parts.append(f'<div class="qso-grid">{_esc(rec.get("grid") or grid)}</div>')
        if detail_line:
            parts.append(f'<div class="qso-detail">{_esc(detail_line)}</div>')
        if extra:
            parts.append(f'<div class="qso-extra">{_esc(" ".join(str(x) for x in extra))}</div>')
        parts.append('</div>')
    parts.append('</div>')
    return ''.join(parts)

def _geojson_geoms_for_bbox(feature, lon_min, lon_max, lat_min, lat_max, tolerance=None):
    """Extract a cartopy Feature's geometries as plain GeoJSON geometry
    dicts, clipped and simplified to the given lon/lat bounding box so the
    embedded --html output stays a reasonable size. Returns [] (background
    for that layer simply omitted) if the geometry can't be read. tolerance
    (degrees) overrides the default simplification, which scales with the box."""
    geoms = []
    try:
        bbox_poly = shapely_box(lon_min, lat_min, lon_max, lat_max)
        span = max(lon_max - lon_min, 0.01)
        if tolerance is None:
            tolerance = max(span / 800, 0.0005)
        for geom in feature.geometries():
            try:
                if not geom.intersects(bbox_poly):
                    continue
                clipped = geom.intersection(bbox_poly)
                if clipped.is_empty:
                    continue
                clipped = clipped.simplify(tolerance, preserve_topology=True)
                geoms.append(shapely_mapping(clipped))
            except Exception:
                continue
    except Exception as e:
        print(f"Warning: could not embed background geography for HTML output ({e})")
    return geoms

def _background_geojson(lon_min, lon_max, lat_min, lat_max):
    """Coastline/borders/land/ocean/lakes geometry for the --html background,
    matching the layers create_grid_map/create_path_map always draw on the
    PNG output (independent of, and in addition to, the optional
    --osm-basemap tile underlay)."""
    return {
        'land': _geojson_geoms_for_bbox(cfeature.LAND, lon_min, lon_max, lat_min, lat_max),
        'ocean': _geojson_geoms_for_bbox(cfeature.OCEAN, lon_min, lon_max, lat_min, lat_max),
        'coastline': _geojson_geoms_for_bbox(cfeature.COASTLINE, lon_min, lon_max, lat_min, lat_max),
        'borders': _geojson_geoms_for_bbox(cfeature.BORDERS, lon_min, lon_max, lat_min, lat_max),
        'lakes': _geojson_geoms_for_bbox(cfeature.LAKES, lon_min, lon_max, lat_min, lat_max),
    }

_LEAFLET_PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  html, body { margin:0; padding:0; height:100%; font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
  #titlebar { background:#1a1a1a; color:#fff; padding:10px 16px; font-size:15px; font-weight:700;
              text-align:center; white-space:pre-line; line-height:1.4; }
  #map-wrap { position:relative; height: calc(100% - 56px); min-height:300px; }
  #map { position:absolute; top:0; bottom:0; left:0; right:0; background:#eef3f7; }
  .field-label { color:#1a3fa0; font-weight:700; font-size:13px;
                 text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff; pointer-events:none; }
  .corner-label { color:#000; font-weight:700; font-size:10px;
                  text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff; pointer-events:none; }
  .home-star { font-size:26px; line-height:26px; color:gold;
               text-shadow: -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000; }
  .station-label { font-weight:700; font-size:11px; background:rgba(255,255,255,0.85);
                    border:none; box-shadow:none; white-space:pre-line; }
  .qso-popup { min-width:150px; }
  .qso-entry { margin-bottom:6px; padding-bottom:6px; border-bottom:1px solid #eee; }
  .qso-entry:last-child { border-bottom:none; margin-bottom:0; padding-bottom:0; }
  .qso-call { font-size:17px; font-weight:700; margin-bottom:2px; }
  .qso-date, .qso-grid, .qso-detail { font-size:12px; color:#333; }
  .qso-extra { font-size:11px; color:#777; font-style:italic; margin-top:2px; }
  #legend { position:absolute; bottom:20px; right:10px; background:rgba(255,255,255,0.92);
            padding:8px 10px; border-radius:4px; box-shadow:0 1px 4px rgba(0,0,0,0.3);
            font-size:12px; z-index:1000; display:none; }
  #legend .bar { width:16px; height:100px; background: linear-gradient(to top, #fff5f0, #a50f15);
                 border:1px solid #999; float:left; margin-right:6px; }
  #legend .labels { display:flex; flex-direction:column; justify-content:space-between; height:100px; }
</style>
</head>
<body>
<div id="titlebar">__TITLEBAR__</div>
<div id="map-wrap"><div id="map"></div><div id="legend"></div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA_JSON__;
(function () {
  var map = L.map('map', { scrollWheelZoom: true });
  var b = DATA.bounds;
  map.fitBounds([[b[0][0], b[0][1]], [b[1][0], b[1][1]]]);

  // OpenStreetMap's tile servers refuse requests without a Referer, which a
  // page opened from a saved file never sends: they'd draw "Access blocked".
  if (DATA.osm && location.protocol === 'file:') {
    map.attributionControl.addAttribution('Street underlay shows when this map is opened from a website');
  } else if (DATA.osm) {
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      opacity: DATA.osm.opacity, maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
  }

  function addGeoms(geoms, style) {
    (geoms || []).forEach(function (g) { L.geoJSON(g, { style: style }).addTo(map); });
  }
  var bg = DATA.background || {};
  addGeoms(bg.ocean, { color: '#7fb8d8', weight: 0, fillColor: '#c6e2f0', fillOpacity: 0.35 });
  addGeoms(bg.land, { color: '#bbb', weight: 0, fillColor: '#e6e6e6', fillOpacity: 0.35 });
  addGeoms(bg.lakes, { color: '#4a90d9', weight: 0.7, fillOpacity: 0 });
  addGeoms(bg.coastline, { color: '#333', weight: 1, fillOpacity: 0 });
  addGeoms(bg.borders, { color: '#666', weight: 0.6, fillOpacity: 0 });

  (DATA.gridOutlines || []).forEach(function (r) {
    L.rectangle([[r[0], r[1]], [r[2], r[3]]], { color: '#ccc', weight: 0.5, fillOpacity: 0 }).addTo(map);
  });

  function bindHoverPopup(layer, popupHtml) {
    layer.bindPopup(popupHtml, { maxWidth: 260 });
    layer.on('mouseover', function () { this.openPopup(); });
    layer.on('mouseout', function () { this.closePopup(); });
  }

  (DATA.rectangles || []).forEach(function (r) {
    var rect = L.rectangle([[r.bounds[0][0], r.bounds[0][1]], [r.bounds[1][0], r.bounds[1][1]]], {
      color: '#000', weight: 0.6, fillColor: r.color, fillOpacity: 0.85
    }).addTo(map);
    bindHoverPopup(rect, r.popup);
  });

  (DATA.fieldLabels || []).forEach(function (l) {
    L.marker([l.lat, l.lon], {
      icon: L.divIcon({ className: 'field-label', html: l.text, iconSize: [30, 18] }),
      interactive: false
    }).addTo(map);
  });
  (DATA.cornerLabels || []).forEach(function (l) {
    L.marker([l.lat, l.lon], {
      icon: L.divIcon({ className: 'corner-label', html: l.text, iconSize: [40, 14] }),
      interactive: false
    }).addTo(map);
  });

  if (DATA.legend) {
    var el = document.getElementById('legend');
    el.style.display = 'block';
    el.innerHTML = '<div style="font-weight:700;margin-bottom:4px;">Contacts</div>' +
      '<div class="bar"></div><div class="labels"><span>' + DATA.legend.max +
      '</span><span>' + DATA.legend.min + '</span></div><div style="clear:both;"></div>';
  }

  (DATA.lines || []).forEach(function (ln) {
    L.polyline(ln, { color: 'crimson', weight: 1.5, opacity: 0.85 }).addTo(map);
  });

  (DATA.markers || []).forEach(function (m) {
    var dot = L.circleMarker([m.lat, m.lon], {
      radius: 6, color: '#000', weight: 0.8, fillColor: 'crimson', fillOpacity: 0.9
    }).addTo(map);
    bindHoverPopup(dot, m.popup);
    dot.bindTooltip(m.label, { permanent: true, direction: 'right', offset: [6, 0], className: 'station-label' });
  });

  if (DATA.home) {
    var star = L.marker([DATA.home.lat, DATA.home.lon], {
      icon: L.divIcon({ className: '', html: '<div class="home-star">★</div>', iconSize: [30, 30], iconAnchor: [15, 15] })
    }).addTo(map);
    star.bindTooltip(DATA.home.label, { permanent: true, direction: 'top', className: 'station-label' });
  }
})();
</script>
</body>
</html>
"""

def _write_leaflet_html(data, output_file, page_title):
    """Render one interactive Leaflet map page from a data dict built by
    create_grid_map_html/create_path_map_html."""
    out = _LEAFLET_PAGE_TEMPLATE.replace('__TITLE__', _esc(page_title))
    out = out.replace('__TITLEBAR__', _esc(data.get('title', page_title)).replace('\n', '<br>'))
    out = out.replace('__DATA_JSON__', json.dumps(data))
    with open(output_file, 'w') as f:
        f.write(out)

def create_path_map(home_grid, contacts, callsign, band, output_file=None,
                     osm_basemap=False, osm_opacity=0.3):
    """Create a local-area 'path map' for a single band: a marker for the home
    station, a marker for each worked station, and a line connecting them,
    labeled with callsign and distance. Meant for small-area contests (e.g.
    ARRL 10 GHz and Up) where the point is to show what's achievable locally,
    not contact density across a continent.

    contacts is a list of (their_callsign, their_grid) tuples for this band.
    osm_basemap adds a faint OpenStreetMap tile underlay (see
    band_is_osm_eligible/add_osm_underlay) for bands at 902 MHz and up; it's
    additive to, and drawn beneath, all of the layers already here.
    """
    home_center = grid_center(home_grid)
    if not home_center:
        print(f"Cannot plot paths for {band}: invalid home grid '{home_grid}'")
        return

    # De-duplicate repeated QSOs with the same station from the same grid
    seen = set()
    unique_contacts = []
    for their_call, their_grid in contacts:
        key = (their_call, their_grid)
        if key in seen:
            continue
        seen.add(key)
        unique_contacts.append((their_call, their_grid))

    if not unique_contacts:
        print(f"No valid worked-station grids to plot for {band}")
        return

    home_lat, home_lon = home_center
    all_grids = [home_grid] + [g for _, g in unique_contacts]
    lon_min, lon_max, lat_min, lat_max = get_optimal_bounds(all_grids)
    region_name = get_region_name(lon_min, lon_max, lat_min, lat_max)

    fig = plt.figure(figsize=(14, 10))
    ax = plt.axes(projection=ccrs.PlateCarree())
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    if osm_basemap and band_is_osm_eligible(band):
        add_osm_underlay(ax, lon_min, lon_max, lat_min, lat_max, opacity=osm_opacity)

    ax.add_feature(cfeature.COASTLINE, linewidth=0.7)
    ax.add_feature(cfeature.BORDERS, linewidth=0.5)
    ax.add_feature(cfeature.LAND, alpha=0.15, color='lightgray')
    ax.add_feature(cfeature.OCEAN, alpha=0.2, color='lightblue')
    ax.add_feature(cfeature.LAKES, edgecolor='blue', facecolor='none', linewidth=0.5)

    # Lines from home station to each worked station, drawn first so markers sit on top
    for their_call, their_grid in unique_contacts:
        center = grid_center(their_grid)
        if not center:
            continue
        their_lat, their_lon = center
        ax.plot([home_lon, their_lon], [home_lat, their_lat],
                 color='crimson', linewidth=1.2, alpha=0.8,
                 transform=ccrs.Geodetic(), zorder=3)

    # Worked-station markers with callsign + distance labels
    for their_call, their_grid in unique_contacts:
        center = grid_center(their_grid)
        if not center:
            continue
        their_lat, their_lon = center
        distance = grid_distance_km(home_grid, their_grid)
        label = their_call
        if distance is not None:
            label += f"\n{distance:.1f} km"

        ax.plot(their_lon, their_lat, marker='o', markersize=7,
                 markerfacecolor='crimson', markeredgecolor='black',
                 markeredgewidth=0.8, transform=ccrs.PlateCarree(), zorder=4)
        ax.text(their_lon, their_lat, f"  {label}", fontsize=7, fontweight='bold',
                 ha='left', va='center', color='black',
                 path_effects=[path_effects.withStroke(linewidth=2, foreground='white')],
                 transform=ccrs.PlateCarree(), zorder=5)

    # Home station marker: distinct gold star, drawn on top of everything else
    ax.plot(home_lon, home_lat, marker='*', markersize=22,
             markerfacecolor='gold', markeredgecolor='black',
             markeredgewidth=1.2, transform=ccrs.PlateCarree(), zorder=6)
    ax.text(home_lon, home_lat, f"  {callsign} ({home_grid})", fontsize=9, fontweight='bold',
             ha='left', va='top', color='black',
             path_effects=[path_effects.withStroke(linewidth=2.5, foreground='white')],
             transform=ccrs.PlateCarree(), zorder=7)

    import matplotlib.ticker as mticker
    gl = ax.gridlines(draw_labels=True, alpha=0.3,
                       xlocs=range(int(lon_min) - 1, int(lon_max) + 2),
                       ylocs=range(int(lat_min) - 1, int(lat_max) + 2))
    gl.xformatter = mticker.FuncFormatter(lambda x, p: f'{abs(int(x))}°W' if x < 0 else f'{int(x)}°E')
    gl.yformatter = mticker.FuncFormatter(lambda y, p: f'{int(y)}°N' if y >= 0 else f'{abs(int(y))}°S')

    distances = [d for d in (grid_distance_km(home_grid, g) for _, g in unique_contacts) if d is not None]
    max_dist = max(distances) if distances else 0

    plt.title(
        f'{callsign} - {band} Band - Contacts from {home_grid}\n'
        f'{region_name.replace("_", " ").title()} — {len(unique_contacts)} contacts, '
        f'longest {max_dist:.1f} km',
        fontsize=13, fontweight='bold')

    plt.tight_layout()

    if not output_file:
        # Home grid is included so multiple operating locations (e.g. a rover)
        # never collide on the same filename for a given band/region.
        output_file = f"{callsign}_{band}_{home_grid}_{region_name}_grid_paths_map.png"

    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close(fig)
    print(f"Path map saved as {output_file}")
    print(f"{band}: {len(unique_contacts)} contacts plotted from home grid {home_grid}, "
          f"longest path {max_dist:.1f} km")

def create_path_map_html(home_grid, contacts, qso_records, callsign, band, output_file=None,
                          osm_basemap=False, osm_opacity=0.3):
    """Interactive HTML counterpart to create_path_map: same home star, lines
    and worked-station dots, but each dot is hoverable/tappable to show a
    popup with that contact's details (callsign, date, six-character grid,
    and other QSO details). Visually matches create_path_map's look,
    including the optional --osm-basemap underlay at the same opacity.

    qso_records is this band/location's full list of QSO detail dicts (see
    parse_cabrillo_grids/parse_csv_grids) — not deduplicated — so a dot
    representing more than one contact to the same grid can show all of them
    in its popup.
    """
    home_center = grid_center(home_grid)
    if not home_center:
        print(f"Cannot plot HTML paths for {band}: invalid home grid '{home_grid}'")
        return

    seen = set()
    unique_contacts = []
    for their_call, their_grid in contacts:
        key = (their_call, their_grid)
        if key in seen:
            continue
        seen.add(key)
        unique_contacts.append((their_call, their_grid))

    if not unique_contacts:
        print(f"No valid worked-station grids to plot for {band} (HTML)")
        return

    records_by_grid = defaultdict(list)
    for rec in (qso_records or []):
        records_by_grid[rec.get('grid')].append(rec)

    home_lat, home_lon = home_center
    all_grids = [home_grid] + [g for _, g in unique_contacts]
    lon_min, lon_max, lat_min, lat_max = get_optimal_bounds(all_grids)
    region_name = get_region_name(lon_min, lon_max, lat_min, lat_max)

    lines = []
    markers = []
    distances = []
    for their_call, their_grid in unique_contacts:
        center = grid_center(their_grid)
        if not center:
            continue
        their_lat, their_lon = center
        lines.append([[home_lat, home_lon], [their_lat, their_lon]])
        distance = grid_distance_km(home_grid, their_grid)
        if distance is not None:
            distances.append(distance)
        label = their_call + (f"\n{distance:.1f} km" if distance is not None else '')
        markers.append({
            'lat': their_lat, 'lon': their_lon, 'label': label,
            'popup': _build_popup_html(their_grid, records_by_grid.get(their_grid, [])),
        })

    max_dist = max(distances) if distances else 0

    data = {
        'title': (f'{callsign} - {band} Band - Contacts from {home_grid}\n'
                  f'{region_name.replace("_", " ").title()} — {len(unique_contacts)} contacts, '
                  f'longest {max_dist:.1f} km'),
        'bounds': [[lat_min, lon_min], [lat_max, lon_max]],
        'osm': {'opacity': osm_opacity} if (osm_basemap and band_is_osm_eligible(band)) else None,
        'background': _background_geojson(lon_min, lon_max, lat_min, lat_max),
        'home': {'lat': home_lat, 'lon': home_lon, 'label': f"{callsign} ({home_grid})"},
        'lines': lines,
        'markers': markers,
    }

    if not output_file:
        output_file = f"{callsign}_{band}_{home_grid}_{region_name}_grid_paths_map.html"

    _write_leaflet_html(data, output_file, f"{callsign} - {band} - {home_grid}")
    print(f"Interactive path map saved as {output_file}")

def create_grid_map(grids, callsign, band, continents=None, output_file=None, location=None,
                     osm_basemap=False, osm_opacity=0.3):
    """Create color-coded map of Maidenhead grid squares for a specific band.

    `location` is an optional home-grid label (e.g. for a rover log with
    multiple operating locations); when given, it's included in the title
    and, unless output_file is set explicitly, in the filename so each
    location gets its own graphic.
    osm_basemap adds a faint OpenStreetMap tile underlay (see
    band_is_osm_eligible/add_osm_underlay) for bands at 902 MHz and up; it's
    additive to, and drawn beneath, all of the layers already here.
    """

    grid_counts = Counter(grids)
    
    # Auto-select continents if not specified
    if continents is None:
        continents = auto_select_continents(grids)
        print(f"Auto-selected continents: {', '.join(continents)}")
    
    # Filter grids by continents
    valid_grids = filter_grids_by_continents(grid_counts, continents)
    
    if not valid_grids:
        print(f"No valid grid squares found for {band} in selected continents")
        return
    
    # Check if we have 6-digit grids (microwave contest)
    has_6digit_grids = any(len(grid) == 6 for grid in valid_grids.keys())
    
    # Get optimal bounds based on actual grid locations
    lon_min, lon_max, lat_min, lat_max = get_optimal_bounds(valid_grids.keys())
    
    # Generate region name based on bounds
    region_name = get_region_name(lon_min, lon_max, lat_min, lat_max)
    
    fig = plt.figure(figsize=(14, 10))
    ax = plt.axes(projection=ccrs.PlateCarree())
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    if osm_basemap and band_is_osm_eligible(band):
        add_osm_underlay(ax, lon_min, lon_max, lat_min, lat_max, opacity=osm_opacity)

    ax.add_feature(cfeature.COASTLINE, linewidth=0.5)
    ax.add_feature(cfeature.BORDERS, linewidth=0.3)
    ax.add_feature(cfeature.LAND, alpha=0.2, color='lightgray')
    ax.add_feature(cfeature.OCEAN, alpha=0.2, color='lightblue')
    ax.add_feature(cfeature.LAKES, edgecolor='blue', facecolor='none', linewidth=0.5)
    
    # Add grid square outlines for VHF/UHF/microwave bands
    if band_has_grid_outlines(band):
        # Draw 1°×2° grid square outlines in light gray
        for lat in range(int(lat_min) - 1, int(lat_max) + 2):
            for lon in range(int(lon_min) - 2, int(lon_max) + 3, 2):
                if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                    rect = patches.Rectangle((lon, lat), 2, 1,
                                           linewidth=0.3, 
                                           edgecolor='lightgray', 
                                           facecolor='none',
                                           alpha=0.7,
                                           transform=ccrs.PlateCarree())
                    ax.add_patch(rect)
    
    # Plot grid squares as rectangles
    max_count = max(valid_grids.values())
    for grid, count in valid_grids.items():
        bounds = maidenhead_to_bounds(grid)
        if bounds:
            grid_lat_min, grid_lat_max, grid_lon_min, grid_lon_max = bounds
            
            intensity = count / max_count
            color = plt.cm.Reds(0.3 + 0.7 * intensity)
            
            rect = patches.Rectangle((grid_lon_min, grid_lat_min), 
                                   grid_lon_max - grid_lon_min, 
                                   grid_lat_max - grid_lat_min,
                                   linewidth=0.5, 
                                   edgecolor='black', 
                                   facecolor=color,
                                   alpha=0.8,
                                   transform=ccrs.PlateCarree())
            ax.add_patch(rect)
    
    # Add 4-digit grid labels at lower-left corner for microwave contests with 6-digit grids
    if has_6digit_grids:
        grid_4digit_positions = {}
        for grid in valid_grids.keys():
            if len(grid) >= 4:
                grid_4digit = grid[:4]
                if grid_4digit not in grid_4digit_positions:
                    bounds = maidenhead_to_bounds(grid_4digit)
                    if bounds:
                        grid_lat_min, grid_lat_max, grid_lon_min, grid_lon_max = bounds
                        # Position at lower-left corner with small offset
                        label_lon = grid_lon_min + (grid_lon_max - grid_lon_min) * 0.05
                        label_lat = grid_lat_min + (grid_lat_max - grid_lat_min) * 0.05
                        grid_4digit_positions[grid_4digit] = (label_lon, label_lat)
        
        for grid_4digit, (lon, lat) in grid_4digit_positions.items():
            ax.text(lon, lat, grid_4digit, fontsize=8, fontweight='bold',
                   ha='left', va='bottom', color='black',
                   path_effects=[path_effects.withStroke(linewidth=2, foreground='white')],
                   transform=ccrs.PlateCarree())
    
    # Add grid field labels - only show if they fit in the visible area
    field_centers = {}
    for grid in valid_grids.keys():
        field = grid[:2]
        if field not in field_centers:
            field_lon_center = (ord(field[0]) - ord('A')) * 20 - 180 + 10
            field_lat_center = (ord(field[1]) - ord('A')) * 10 - 90 + 5
            # Only show labels if they're in the visible area
            if lon_min <= field_lon_center <= lon_max and lat_min <= field_lat_center <= lat_max:
                field_centers[field] = (field_lon_center, field_lat_center)
    
    for field, (lon, lat) in field_centers.items():
        ax.text(lon, lat, field, fontsize=12, fontweight='bold',
                ha='center', va='center', color='blue',
                transform=ccrs.PlateCarree())
    
    # Configure gridlines with whole degree increments
    import matplotlib.ticker as mticker
    gl = ax.gridlines(draw_labels=True, alpha=0.3, 
                     xlocs=range(int(lon_min), int(lon_max) + 1),
                     ylocs=range(int(lat_min), int(lat_max) + 1))
    gl.xformatter = mticker.FuncFormatter(lambda x, p: f'{abs(int(x))}°W' if x < 0 else f'{int(x)}°E')
    gl.yformatter = mticker.FuncFormatter(lambda y, p: f'{int(y)}°N' if y >= 0 else f'{abs(int(y))}°S')
    
    title = f'{callsign} - {band} Band - Maidenhead Grid Squares\n{region_name.replace("_", " ").title()}'
    if location:
        title = f'{callsign} - {band} Band - Maidenhead Grid Squares\nFrom {location} — {region_name.replace("_", " ").title()}'
    plt.title(title, fontsize=14, fontweight='bold')

    # Add colorbar with whole-number ticks only (fractional contacts aren't a thing)
    sm = plt.cm.ScalarMappable(cmap=plt.cm.Reds,
                               norm=plt.Normalize(vmin=1, vmax=max_count))
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, shrink=0.6)
    cbar.set_label('Number of Contacts', fontsize=12)
    cbar.locator = mticker.MaxNLocator(integer=True)
    cbar.update_ticks()

    plt.tight_layout()

    if not output_file:
        if location:
            output_file = f"{callsign}_{band}_{location}_{region_name}_maidenhead_map.png"
        else:
            output_file = f"{callsign}_{band}_{region_name}_maidenhead_map.png"

    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close(fig)
    print(f"Map saved as {output_file}")
    print(f"{band}: {len(valid_grids)} unique grid squares, {sum(valid_grids.values())} contacts")

def create_grid_map_html(grids, qso_records, callsign, band, continents=None, output_file=None,
                          location=None, osm_basemap=False, osm_opacity=0.3):
    """Interactive HTML counterpart to create_grid_map: same color-coded grid
    squares, field labels and (for 6-character grids) corner labels, but
    each grid square is hoverable/tappable to show a popup listing the QSOs
    worked there (callsign, date, six-character grid, other QSO details).
    Visually matches create_grid_map's look, including the optional
    --osm-basemap underlay at the same opacity.

    qso_records is this band/location's full list of QSO detail dicts (see
    parse_cabrillo_grids/parse_csv_grids).
    """
    grid_counts = Counter(grids)

    if continents is None:
        continents = auto_select_continents(grids)

    valid_grids = filter_grids_by_continents(grid_counts, continents)
    if not valid_grids:
        print(f"No valid grid squares found for {band} in selected continents (HTML)")
        return

    has_6digit_grids = any(len(grid) == 6 for grid in valid_grids.keys())
    lon_min, lon_max, lat_min, lat_max = get_optimal_bounds(valid_grids.keys())
    region_name = get_region_name(lon_min, lon_max, lat_min, lat_max)

    records_by_grid = defaultdict(list)
    for rec in (qso_records or []):
        records_by_grid[rec.get('grid')].append(rec)

    grid_outlines = []
    if band_has_grid_outlines(band):
        for lat in range(int(lat_min) - 1, int(lat_max) + 2):
            for lon in range(int(lon_min) - 2, int(lon_max) + 3, 2):
                if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                    grid_outlines.append([lat, lon, lat + 1, lon + 2])

    max_count = max(valid_grids.values())
    rectangles = []
    for grid, count in valid_grids.items():
        bounds = maidenhead_to_bounds(grid)
        if not bounds:
            continue
        grid_lat_min, grid_lat_max, grid_lon_min, grid_lon_max = bounds
        intensity = count / max_count
        color = mcolors.to_hex(plt.cm.Reds(0.3 + 0.7 * intensity))
        rectangles.append({
            'bounds': [[grid_lat_min, grid_lon_min], [grid_lat_max, grid_lon_max]],
            'color': color,
            'popup': _build_popup_html(grid, records_by_grid.get(grid, [])),
        })

    corner_labels = []
    if has_6digit_grids:
        seen_4digit = set()
        for grid in valid_grids.keys():
            if len(grid) >= 4:
                grid_4digit = grid[:4]
                if grid_4digit in seen_4digit:
                    continue
                seen_4digit.add(grid_4digit)
                bounds = maidenhead_to_bounds(grid_4digit)
                if bounds:
                    g_lat_min, g_lat_max, g_lon_min, g_lon_max = bounds
                    corner_labels.append({
                        'lat': g_lat_min + (g_lat_max - g_lat_min) * 0.05,
                        'lon': g_lon_min + (g_lon_max - g_lon_min) * 0.05,
                        'text': grid_4digit,
                    })

    field_labels = []
    seen_fields = set()
    for grid in valid_grids.keys():
        field = grid[:2]
        if field in seen_fields:
            continue
        field_lon_center = (ord(field[0]) - ord('A')) * 20 - 180 + 10
        field_lat_center = (ord(field[1]) - ord('A')) * 10 - 90 + 5
        if lon_min <= field_lon_center <= lon_max and lat_min <= field_lat_center <= lat_max:
            seen_fields.add(field)
            field_labels.append({'lat': field_lat_center, 'lon': field_lon_center, 'text': field})

    title = f'{callsign} - {band} Band - Maidenhead Grid Squares\n{region_name.replace("_", " ").title()}'
    if location:
        title = f'{callsign} - {band} Band - Maidenhead Grid Squares\nFrom {location} — {region_name.replace("_", " ").title()}'

    data = {
        'title': title,
        'bounds': [[lat_min, lon_min], [lat_max, lon_max]],
        'osm': {'opacity': osm_opacity} if (osm_basemap and band_is_osm_eligible(band)) else None,
        'background': _background_geojson(lon_min, lon_max, lat_min, lat_max),
        'gridOutlines': grid_outlines,
        'rectangles': rectangles,
        'fieldLabels': field_labels,
        'cornerLabels': corner_labels,
        'legend': {'min': 1, 'max': max_count},
    }

    if not output_file:
        if location:
            output_file = f"{callsign}_{band}_{location}_{region_name}_maidenhead_map.html"
        else:
            output_file = f"{callsign}_{band}_{region_name}_maidenhead_map.html"

    _write_leaflet_html(data, output_file, f"{callsign} - {band} - {region_name}")
    print(f"Interactive map saved as {output_file}")
    print(f"{band}: {len(valid_grids)} unique grid squares, {sum(valid_grids.values())} contacts (HTML)")

# ---------------------------------------------------------------------------
# --animate: a "movie" of a whole contest log
# ---------------------------------------------------------------------------
# One self-contained Leaflet page that replays the log in time order: the map
# follows the station from one operating location to the next while a line to
# each worked station is drawn in as the contact is made, colored by band.
#
# Band styling. Each contest band has a fixed color (never re-assigned when a
# log uses fewer bands), taken from a dark-surface categorical palette that
# passes the adjacent-pair color-vision checks. Any two bands can be on screen
# together, so color is never the only cue: every band also has its own line
# dash pattern and endpoint marker shape, and the legend names and counts them.
_ANIMATION_BAND_STYLES = [
    # band,  color,     dash pattern,   marker shape
    ('10G',  '#3987e5', None,           'circle'),
    ('24G',  '#d95926', '9 5',          'square'),
    ('47G',  '#199e70', '2 4',          'diamond'),
    ('75G',  '#c98500', '10 4 2 4',     'triangle'),
    ('123G', '#d55181', '15 6',         'triangle-down'),
    ('134G', '#008300', '4 3',          'hexagon'),
    ('241G', '#9085e9', '12 3 3 3 3 3', 'plus'),
    ('300G', '#e66767', '1 5',          'star'),
]
_ANIMATION_OTHER_COLOR = '#8f948f'
_ANIMATION_OTHER_MARKERS = ['circle', 'square', 'diamond', 'triangle', 'triangle-down', 'hexagon', 'plus', 'star']
_ANIMATION_OTHER_DASHES = [None, '9 5', '2 4', '10 4 2 4', '15 6', '4 3', '12 3 3 3 3 3', '1 5']

# Playback timing, in animation seconds at 1x speed.
_ANIM_LEAD = 2.0          # title card before the first contact
_ANIM_TAIL = 3.0          # hold on the finished picture
_ANIM_MIN_SPACING = 0.15  # so a burst of same-minute contacts still plays one after another
_ANIM_IDLE_GAP = 1.0      # a long break at the same location, fast-forwarded
_ANIM_MOVE_BASE = 1.8     # relocating to a new operating grid...
_ANIM_MOVE_PER_KM = 1 / 300.0   # ...plus a little longer for longer drives
_ANIM_MOVE_MAX = 4.5
_ANIM_WEEKEND_EXTRA = 1.6 # extra beat for the title card when a new weekend starts


def default_animation_seconds(n_contacts, n_moves):
    """Movie length at 1x when --animate-seconds isn't given: about two
    minutes for a busy multi-location log, under a minute for a small one."""
    return max(40.0, min(150.0, 30.0 + 0.3 * n_contacts + 2.5 * n_moves))


# ARRL 10 GHz and Up scoring for the movie's running score. It mirrors the
# 10ghz-log-analyzer (arrl_10ghz_cabrillo.calculate_score) exactly, so the
# movie ends on the same number as that tool's summary: per QSO, distance in
# km rounded up (minimum 1) times the band multiplier (rules 5.2: 10 GHz x1,
# 24 x2, 47 x3, 75 x4, 122 GHz and up x5), plus 100 the first time each call
# is worked on each band. Distances use the analyzer's own grid convention
# (sub-square centers; a 4-character grid is read as its "AA" sub-square)
# and a 6371 km Earth radius, which differ slightly from the map's.
_SCORED_CONTESTS = {'ARRL-10-GHZ': 'ARRL 10 GHz and Up'}


def _arrl10_multiplier(band):
    freq = band_min_freq_mhz(band) or 0
    for floor, multiplier in ((119980, 5), (75000, 4), (47000, 3), (24000, 2)):
        if freq >= floor:
            return multiplier
    return 1


def _arrl10_latlon(grid):
    grid = str(grid).upper().strip()
    if len(grid) < 4:
        return 0.0, 0.0
    grid = (grid + 'AA')[:6]
    try:
        lon = (ord(grid[0]) - ord('A')) * 20 - 180 + (ord(grid[2]) - ord('0')) * 2 + (ord(grid[4]) - ord('A')) * 5 / 60
        lat = (ord(grid[1]) - ord('A')) * 10 - 90 + (ord(grid[3]) - ord('0')) + (ord(grid[5]) - ord('A')) * 2.5 / 60
        return lat + 1.25 / 60, lon + 2.5 / 60
    except (ValueError, IndexError):
        return 0.0, 0.0


def _arrl10_points(home, grid, band):
    lat1, lon1 = map(math.radians, _arrl10_latlon(home))
    lat2, lon2 = map(math.radians, _arrl10_latlon(grid))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    km = 6371 * 2 * math.asin(math.sqrt(a))
    return max(1, math.ceil(km)) * _arrl10_multiplier(band)


def _parse_qso_datetime(date_text, time_text):
    """UTC datetime from a QSO's date (YYYY-MM-DD) and time (HHMM, HH:MM,
    HH:MM:SS or a bare number such as 937 or 937.0). Returns None if either
    can't be understood."""
    try:
        date_part = datetime.strptime(str(date_text).strip()[:10], '%Y-%m-%d')
        time_text = str(time_text).strip()
        if re.fullmatch(r'\d+\.0*', time_text):   # 937.0 from a spreadsheet column
            time_text = time_text.split('.')[0]
        digits = ''.join(ch for ch in time_text if ch.isdigit())
        if not digits:
            return None
        digits = digits.zfill(4)[:4]
        hours, minutes = int(digits[:2]), int(digits[2:])
        if hours > 23 or minutes > 59:
            return None
        return date_part.replace(hour=hours, minute=minutes)
    except (ValueError, TypeError):
        return None


def _animation_band_styles(bands):
    """Style dicts (band, color, dash, marker) for the bands in a log, in
    ascending frequency order. Contest bands keep their fixed style."""
    fixed = {b: (c, d, m) for b, c, d, m in _ANIMATION_BAND_STYLES}
    ordered = sorted(set(bands), key=lambda b: (band_min_freq_mhz(b) is None, band_min_freq_mhz(b) or 0, b))
    free_slots = [s for s in _ANIMATION_BAND_STYLES if s[0] not in ordered]
    styles = []
    for band in ordered:
        if band in fixed:
            color, dash, marker = fixed[band]
        elif free_slots:
            _, color, dash, marker = free_slots.pop(0)
        else:
            n = len(styles)
            color, dash, marker = (_ANIMATION_OTHER_COLOR, _ANIMATION_OTHER_DASHES[n % 8],
                                   _ANIMATION_OTHER_MARKERS[n % 8])
        styles.append({'band': band, 'color': color, 'dash': dash, 'marker': marker})
    return styles


def _animation_total_seconds(gaps, spans, k):
    return _ANIM_LEAD + _ANIM_TAIL + sum(gaps) + sum(max(_ANIM_MIN_SPACING, m * k) for m in spans)


def build_animation_data(qso_records_by_band, fallback_home_grid=None, target_seconds=None,
                         gap_minutes=45.0, contest=None):
    """Turn a log into the timeline the --animate page plays.

    qso_records_by_band is what parse_cabrillo_grids/parse_csv_grids return.
    Every worked contact becomes an event on one clock. Quiet stretches (more
    than gap_minutes with no contact) and relocations are fast-forwarded, and
    the rest of the time is stretched so the whole movie lasts about
    target_seconds at 1x (default: default_animation_seconds). Operating days are counted by the station's local
    solar date (UTC shifted by longitude/15 hours), so an evening session that
    crosses 0000Z stays on its own day; consecutive days form a weekend, and
    all weekends play back to back as one period.

    For a contest whose scoring rules are known (contest = the Cabrillo
    CONTEST value, e.g. ARRL-10-GHZ), each event also carries the running
    score after it ('sc'), and data['scoring'] names the rules.

    Returns None if the log has no contact with a time, a worked grid and an
    operating grid. Otherwise a JSON-ready dict (see create_animation_html).
    """
    flat, skipped = [], 0
    for band, records in qso_records_by_band.items():
        for rec in records:
            home = (rec.get('home_grid') or fallback_home_grid or '').upper() or None
            when = _parse_qso_datetime(rec.get('date'), rec.get('time'))
            there = grid_center(rec['grid']) if rec.get('grid') else None
            here = grid_center(home) if home else None
            if not (when and there and here):
                skipped += 1
                continue
            flat.append({
                'order': len(flat), 'band': band, 'call': rec.get('call') or 'Unknown',
                'grid': rec['grid'].upper(), 'home': home, 'when': when,
                'lat': there[0], 'lon': there[1], 'hlat': here[0], 'hlon': here[1],
                'km': grid_distance_km(home, rec['grid']),
            })
    if not flat:
        return None
    flat.sort(key=lambda e: (e['when'], e['order']))

    # Operating stops: runs of consecutive contacts from the same grid. Going
    # back to an earlier grid later in the log is a new stop.
    stops = []
    for event in flat:
        if not stops or stops[-1]['grid'] != event['home']:
            stops.append({'grid': event['home'], 'lat': event['hlat'], 'lon': event['hlon'],
                          'first': None, 'last': None, 'n': 0})
        event['stop'] = len(stops) - 1

    def solar_date(event):
        return (event['when'] + timedelta(hours=event['hlon'] / 15.0)).date()

    # Days -> weekends.
    dates = sorted({solar_date(e) for e in flat})
    day_of = {d: i for i, d in enumerate(dates)}
    weekend_of, weekend = {}, 0
    for i, d in enumerate(dates):
        if i and (d - dates[i - 1]).days > 1:
            weekend += 1
        weekend_of[d] = weekend
    for event in flat:
        d = solar_date(event)
        event['day'], event['weekend'] = day_of[d], weekend_of[d]

    # Timeline. Between consecutive contacts there's either live action, a
    # quiet stretch, a relocation or the jump to a new weekend. Fixed-length
    # gaps are fixed; the live action is scaled so the movie hits the target.
    kinds, gap_secs, span_mins, move_km = [None], [], [], [0.0]
    for prev, cur in zip(flat, flat[1:]):
        span = (cur['when'] - prev['when']).total_seconds() / 60.0
        moved = cur['stop'] != prev['stop']
        km = grid_distance_km(prev['home'], cur['home']) if moved else 0.0
        move_km.append(km)
        if cur['weekend'] != prev['weekend']:
            kind = 'weekend'
            secs = (min(_ANIM_MOVE_MAX, _ANIM_MOVE_BASE + km * _ANIM_MOVE_PER_KM) if moved
                    else 2.5) + _ANIM_WEEKEND_EXTRA
        elif moved:
            kind, secs = 'move', min(_ANIM_MOVE_MAX, _ANIM_MOVE_BASE + km * _ANIM_MOVE_PER_KM)
        elif span > gap_minutes:
            kind, secs = 'idle', _ANIM_IDLE_GAP
        else:
            kind, secs = 'active', None
        kinds.append(kind)
        if secs is None:
            span_mins.append(span)
        else:
            gap_secs.append(secs)
    if target_seconds is None:
        target_seconds = default_animation_seconds(len(flat), len(stops) - 1)
    lo, hi = 0.0, 1000.0
    if _animation_total_seconds(gap_secs, span_mins, 0.0) < target_seconds:
        for _ in range(60):
            mid = (lo + hi) / 2
            if _animation_total_seconds(gap_secs, span_mins, mid) > target_seconds:
                hi = mid
            else:
                lo = mid
    k = lo

    times, t = [], _ANIM_LEAD
    gap_iter, span_iter = iter(gap_secs), iter(span_mins)
    for i, kind in enumerate(kinds):
        if i:
            t += next(gap_iter) if kind != 'active' else max(_ANIM_MIN_SPACING, next(span_iter) * k)
        times.append(t)
    duration = t + _ANIM_TAIL

    # Relocation glides, and the labelled gaps the page shows while fast-forwarding.
    moves, segments = [], []
    for i in range(1, len(flat)):
        a, b = flat[i - 1], flat[i]
        if kinds[i] in ('move', 'weekend', 'idle'):
            segments.append({
                't0': round(times[i - 1], 3), 't1': round(times[i], 3), 'kind': kinds[i],
                'from': a['stop'], 'to': b['stop'], 'km': round(move_km[i]),
                'minutes': round((b['when'] - a['when']).total_seconds() / 60.0),
                'weekend': b['weekend'] + 1,
            })
        if a['stop'] != b['stop']:
            moves.append({'t0': round(times[i - 1] + 0.5, 3), 't1': round(times[i] - 0.6, 3),
                          'from': a['stop'], 'to': b['stop']})

    # Bands, stops and days.
    bands = _animation_band_styles(e['band'] for e in flat)
    band_index = {b['band']: i for i, b in enumerate(bands)}
    for b in bands:
        b['n'] = 0
    for i, e in enumerate(flat):
        bands[band_index[e['band']]]['n'] += 1
        s = stops[e['stop']]
        if s['first'] is None:
            s['first'], s['counts'] = i, [0] * len(bands)
        s['last'] = i
        s['n'] += 1
        s['counts'][band_index[e['band']]] += 1
    epoch = lambda dtm: int((dtm - datetime(1970, 1, 1)).total_seconds() // 60)
    for s in stops:
        s['t'] = round(times[s['first']], 3)
        s['utc0'], s['utc1'] = epoch(flat[s['first']]['when']), epoch(flat[s['last']]['when'])
        s['day0'], s['day1'] = flat[s['first']]['day'], flat[s['last']]['day']
    days = []
    for di, d in enumerate(dates):
        first = next(i for i, e in enumerate(flat) if e['day'] == di)
        days.append({'label': d.strftime('%a %b ') + str(d.day), 'weekend': weekend_of[d] + 1,
                     'first': first, 't': round(times[first], 3)})

    events = [{
        't': round(times[i], 3), 'lat': round(e['lat'], 4), 'lon': round(e['lon'], 4),
        'call': e['call'], 'grid': e['grid'], 'band': band_index[e['band']], 'stop': e['stop'],
        'km': round(e['km']), 'utc': epoch(e['when']), 'day': e['day'],
    } for i, e in enumerate(flat)]
    scoring = _SCORED_CONTESTS.get((contest or '').upper())
    if scoring:
        score, worked = 0, set()
        for event, e in zip(events, flat):
            score += _arrl10_points(e['home'], e['grid'], e['band'])
            if (e['call'].upper(), e['band']) not in worked:
                worked.add((e['call'].upper(), e['band']))
                score += 100
            event['sc'] = score
    clock = [[round(times[0] - _ANIM_LEAD, 3), events[0]['utc']]] + [[ev['t'], ev['utc']] for ev in events]

    # Camera: three fixed zoom levels. "Area" is the one where a typical long
    # contact (90th percentile) still fits comfortably around the station.
    kms = sorted(e['km'] for e in events)
    reach_km = max(25.0, kms[min(len(kms) - 1, int(len(kms) * 0.9))])
    mid_lat = sum(s['lat'] for s in stops) / len(stops)
    area = math.floor(math.log2(156543.03 * math.cos(math.radians(mid_lat)) * 260 / (reach_km * 1000.0)))
    area = max(5, min(11, area))
    zooms = {'region': area - 1, 'area': area, 'local': area + 1}

    points = [(s['lat'], s['lon']) for s in stops] + [(e['lat'], e['lon']) for e in events]
    return {
        'events': events, 'stops': stops, 'bands': bands, 'days': days, 'moves': moves,
        'segments': segments, 'clock': clock, 'duration': round(duration, 3), 'zooms': zooms,
        'scoring': {'rules': scoring, 'total': events[-1]['sc']} if scoring else None,
        'bounds': [[min(p[0] for p in points), min(p[1] for p in points)],
                   [max(p[0] for p in points), max(p[1] for p in points)]],
        'totals': {'qsos': len(events), 'calls': len({e['call'] for e in events}),
                   'stops': len(stops), 'days': len(days), 'weekends': weekend + 1,
                   'skipped': skipped, 'secondsPerMinute': round(k, 4)},
    }


_ANIMATION_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
__LEAFLET_CSS__
<style>
  :root {
    color-scheme: dark;
    --surface-0: #0e1217;      /* page, ocean */
    --surface-1: #1f242b;      /* land */
    --panel: rgba(14, 18, 23, 0.88);
    --panel-edge: #2c333c;
    --ink-1: #f0efec;
    --ink-2: #c3c2b7;
    --ink-3: #8f948f;
    --focus: #f0efec;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--surface-0); color: var(--ink-1);
    font: 14px/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; overflow: hidden; }
  [hidden] { display: none !important; }
  #map { position: absolute; inset: 0; background: var(--surface-0); }
  .leaflet-container { background: var(--surface-0); font: inherit; }
  .leaflet-control-attribution { background: rgba(14,18,23,.7) !important; color: var(--ink-3) !important; font-size: 10px; }
  .leaflet-control-attribution a { color: var(--ink-2) !important; }
  .panel { position: absolute; z-index: 1000; background: var(--panel); border: 1px solid var(--panel-edge);
    border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.35); backdrop-filter: blur(6px); }
  button { font: inherit; color: var(--ink-1); background: transparent; border: 1px solid var(--panel-edge);
    border-radius: 7px; padding: 5px 10px; cursor: pointer; }
  button:hover { border-color: var(--ink-3); }
  button[aria-pressed="true"] { background: var(--ink-1); color: var(--surface-0); border-color: var(--ink-1); }
  button:focus-visible, input:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }

  #hud { top: 14px; left: 14px; padding: 12px 14px 12px; width: 268px; }
  #hud .who { font-size: 12px; font-weight: 600; letter-spacing: .02em; color: var(--ink-3); }
  #hud .day { margin-top: 6px; font-size: 13px; color: var(--ink-2); }
  #hud .clock { font: 600 30px/1.1 ui-monospace, "SF Mono", Menlo, Consolas, monospace; letter-spacing: .02em; }
  #hud .clock small { font-size: 13px; color: var(--ink-3); margin-left: 4px; }
  #hud .where { margin-top: 8px; font-size: 13px; color: var(--ink-2); min-height: 19px; }
  #hud .where b { color: var(--ink-1); font-weight: 600; }
  #hud .stats { display: flex; gap: 18px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--panel-edge); }
  #hud .stat .n { font: 600 20px/1.1 ui-monospace, Menlo, Consolas, monospace; }
  #hud .stat .l { font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .05em; }
  #hud .score { margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--panel-edge); font-size: 13px; color: var(--ink-2); }
  #hud .score b { font: 600 18px/1 ui-monospace, Menlo, Consolas, monospace; color: var(--ink-1); margin-left: 4px; }

  #legend { top: 14px; right: 14px; padding: 10px 10px 8px; width: 200px; }
  #legend h2, #table-panel h2 { margin: 0 4px 6px; font-size: 11px; font-weight: 600; letter-spacing: .06em;
    text-transform: uppercase; color: var(--ink-3); }
  .leg { display: flex; align-items: center; gap: 8px; width: 100%; border: 1px solid transparent;
    padding: 4px 6px; margin-bottom: 2px; text-align: left; }
  .leg[aria-pressed="false"] { opacity: .4; }
  .leg[aria-pressed="false"] .name { text-decoration: line-through; }
  .leg[aria-pressed] { background: transparent; color: var(--ink-1); }
  .leg .name { flex: 1; }
  .leg .count { font-family: ui-monospace, Menlo, Consolas, monospace; color: var(--ink-2); }
  #legend .hint { margin: 6px 4px 0; font-size: 11px; color: var(--ink-3); }

  #zoom { top: auto; right: 14px; bottom: 96px; padding: 6px; display: flex; gap: 6px; }

  #controls { left: 14px; right: 14px; bottom: 14px; padding: 10px 12px; display: flex; align-items: center; gap: 10px; }
  #controls .group { display: flex; gap: 4px; }
  #play { min-width: 44px; font-size: 16px; }
  #track { position: relative; flex: 1; height: 36px; }
  #scrub { position: absolute; left: 0; right: 0; top: 0; width: 100%; height: 22px; margin: 0; accent-color: var(--ink-1); }
  #ticks { position: absolute; left: 0; right: 0; top: 21px; height: 14px; pointer-events: none; }
  #ticks span { position: absolute; transform: translateX(-1px); border-left: 1px solid var(--ink-3);
    height: 6px; font-size: 10px; color: var(--ink-3); padding-left: 3px; white-space: nowrap; line-height: 13px; }
  #remain { font-family: ui-monospace, Menlo, Consolas, monospace; color: var(--ink-2); min-width: 46px; text-align: right; }

  #banner { left: 50%; top: 14px; transform: translateX(-50%); padding: 7px 14px; font-size: 13px; color: var(--ink-2);
    white-space: nowrap; transition: opacity .25s; }
  #card { left: 50%; top: 38%; transform: translate(-50%, -50%); padding: 18px 28px; text-align: center;
    pointer-events: none; transition: opacity .35s; }
  #card .k { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--ink-3); }
  #card .v { font-size: 26px; font-weight: 700; margin-top: 2px; }
  #start { left: 50%; top: 50%; transform: translate(-50%, -50%); padding: 24px 28px; text-align: center; max-width: 92vw; }
  #start h1 { margin: 0 0 6px; font-size: 22px; }
  #start p { margin: 0 0 16px; color: var(--ink-2); }
  #start button { font-size: 16px; padding: 10px 22px; background: var(--ink-1); color: var(--surface-0); border-color: var(--ink-1); }

  #table-panel { right: 14px; top: 14px; max-height: calc(100vh - 110px); width: min(700px, calc(100vw - 28px));
    padding: 12px; overflow: auto; z-index: 1100; }
  #table-panel table { border-collapse: collapse; width: 100%; font-size: 12px; }
  #table-panel th, #table-panel td { padding: 5px 7px; border-bottom: 1px solid var(--panel-edge); text-align: right; white-space: nowrap; }
  #table-panel th { color: var(--ink-3); font-weight: 600; }
  #table-panel th:nth-child(-n+4), #table-panel td:nth-child(-n+4) { text-align: left; }
  #table-panel .close { float: right; }

  .qso-mark svg, .station svg { display: block; overflow: visible; }
  .qso-tip, .stop-tip { background: var(--panel); color: var(--ink-1); border: 1px solid var(--panel-edge);
    border-radius: 6px; box-shadow: none; font-size: 12px; }
  .qso-tip::before, .stop-tip::before { display: none; }
  .station-tip { background: var(--ink-1); color: var(--surface-0); border: none; font-weight: 700;
    font-size: 12px; padding: 2px 6px; box-shadow: 0 2px 8px rgba(0,0,0,.5); }
  .station-tip::before { border-top-color: var(--ink-1) !important; }
  .station .ring { position: absolute; left: 50%; top: 50%; width: 34px; height: 34px; margin: -17px 0 0 -17px;
    border: 2px solid var(--ink-1); border-radius: 50%; opacity: 0; animation: ping 2.4s ease-out infinite; }
  @keyframes ping { 0% { transform: scale(.35); opacity: .8; } 100% { transform: scale(1.4); opacity: 0; } }
  .call-flash { color: var(--ink-1); font-weight: 700; font-size: 12px; white-space: nowrap;
    text-shadow: 0 0 3px #000, 0 0 3px #000, 0 0 6px #000; animation: flash 2.2s ease-out forwards; pointer-events: none; }
  @keyframes flash { 0% { opacity: 0; transform: translateY(4px); } 12% { opacity: 1; transform: none; }
    70% { opacity: 1; } 100% { opacity: 0; } }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

  @media (prefers-reduced-motion: reduce) {
    .station .ring, .call-flash { animation: none; }
    .call-flash { display: none; }
  }
  @media (max-width: 720px) {
    #hud { top: 10px; left: 10px; right: 10px; width: auto; padding: 9px 12px; }
    #hud .who { font-size: 11px; }
    #hud .day { margin-top: 2px; font-size: 12px; }
    #hud .clock { font-size: 22px; }
    #hud .where { margin-top: 4px; font-size: 12px; }
    #hud .stats { gap: 16px; margin-top: 6px; padding-top: 6px; }
    #hud .stat .n { font-size: 16px; }
    #hud .score { margin-top: 6px; padding-top: 6px; font-size: 12px; }
    #hud .score b { font-size: 15px; }
    #legend { top: auto; right: 10px; left: 10px; bottom: 112px; width: auto; padding: 5px;
      display: flex; flex-wrap: wrap; gap: 2px; }
    #legend h2, #legend .hint { display: none; }
    #legend-rows { display: contents; }
    .leg { width: auto; margin: 0; padding: 2px 6px; gap: 5px; font-size: 12px; }
    .leg svg { width: 32px; }
    #zoom { top: 50%; bottom: auto; right: 10px; transform: translateY(-50%); flex-direction: column; padding: 4px; gap: 4px; }
    #zoom button { padding: 5px 8px; font-size: 12px; }
    #banner { top: auto; bottom: 168px; max-width: calc(100vw - 20px); overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
    #controls { left: 10px; right: 10px; bottom: 10px; padding: 8px 10px 6px; flex-wrap: wrap; gap: 6px; }
    #controls button { padding: 4px 8px; }
    #play { min-width: 40px; }
    #track { order: 9; flex-basis: 100%; height: 34px; }
    #remain { margin-left: auto; min-width: 0; }
    #table-panel { left: 10px; right: 10px; width: auto; top: 10px; max-height: calc(100vh - 120px); }
  }
</style>
</head>
<body>
<div id="map" role="img" aria-label="__TITLE__"></div>

<div id="hud" class="panel">
  <div class="who" id="who"></div>
  <div class="day" id="day"></div>
  <div class="clock"><span id="clock">--:--</span><small id="clock-date">UTC</small></div>
  <div class="where" id="where"></div>
  <div class="stats">
    <div class="stat"><div class="n" id="n-qsos">0</div><div class="l">QSOs</div></div>
    <div class="stat"><div class="n" id="n-calls">0</div><div class="l">Stations</div></div>
    <div class="stat"><div class="n" id="n-stop">0</div><div class="l">Location</div></div>
  </div>
  <div class="score" id="score-line" hidden>Cumulative Score: <b id="score">0</b></div>
</div>

<div id="legend" class="panel" aria-label="Bands">
  <h2>Bands</h2>
  <div id="legend-rows"></div>
  <p class="hint">Click a band to hide or show it.</p>
</div>

<div id="zoom" class="panel" role="group" aria-label="Zoom level">
  <button type="button" data-zoom="region">Region</button>
  <button type="button" data-zoom="area">Area</button>
  <button type="button" data-zoom="local">Local</button>
</div>

<div id="banner" class="panel" hidden></div>
<div id="card" class="panel" hidden><div class="k" id="card-k"></div><div class="v" id="card-v"></div></div>

<div id="controls" class="panel">
  <button type="button" id="play" aria-label="Play">&#9654;</button>
  <button type="button" id="restart" aria-label="Restart">&#8634;</button>
  <div class="group" role="group" aria-label="Playback speed">
    <button type="button" data-speed="0.5">0.5&times;</button>
    <button type="button" data-speed="1" aria-pressed="true">1&times;</button>
    <button type="button" data-speed="2">2&times;</button>
  </div>
  <div id="track">
    <input id="scrub" type="range" min="0" max="1000" step="1" value="0" aria-label="Position in the contest">
    <div id="ticks"></div>
  </div>
  <span id="remain" aria-label="Time remaining">0:00</span>
  <button type="button" id="table-btn" aria-expanded="false" aria-controls="table-panel">Table</button>
</div>

<div id="table-panel" class="panel" hidden>
  <button type="button" class="close" id="table-close">Close</button>
  <h2>Operating locations</h2>
  <table id="table"></table>
</div>

<div id="start" class="panel">
  <h1 id="start-title"></h1>
  <p id="start-sub"></p>
  <button type="button" id="start-btn">&#9654;&ensp;Play</button>
</div>
<div id="live" class="sr-only" aria-live="polite"></div>

__LEAFLET_JS__
<script>
const DATA = __DATA_JSON__;
(function () {
  'use strict';
  var D = DATA, E = D.events, S = D.stops, B = D.bands, DUR = D.duration;
  var $ = function (id) { return document.getElementById(id); };
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var GROW = 0.45;          // animation seconds for a contact line to draw in
  var MAX_FLASHES = 10;

  function text(id, s) { var el = $(id); if (el.textContent !== s) el.textContent = s; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function utcLabel(min) { var d = new Date(min * 60000); return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()); }
  function utcDate(min) { var d = new Date(min * 60000); return MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate(); }
  function utcStamp(min) { return utcDate(min) + ' ' + utcLabel(min) + 'Z'; }
  function utcSpan(a, b) {
    return utcDate(a) === utcDate(b) ? utcDate(a) + ' ' + utcLabel(a) + '–' + utcLabel(b) + 'Z'
                                     : utcStamp(a) + ' – ' + utcStamp(b);
  }
  function mmss(sec) { sec = Math.max(0, Math.round(sec)); return Math.floor(sec / 60) + ':' + pad(sec % 60); }
  function ease(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function lerp(a, b, f) { return a + (b - a) * f; }

  // Marker shapes: the second, color-independent cue for each band.
  var SHAPES = {
    'circle': '<circle r="4.2"/>',
    'square': '<rect x="-3.8" y="-3.8" width="7.6" height="7.6"/>',
    'diamond': '<polygon points="0,-5.2 5.2,0 0,5.2 -5.2,0"/>',
    'triangle': '<polygon points="0,-5.2 5,4 -5,4"/>',
    'triangle-down': '<polygon points="0,5.2 5,-4 -5,-4"/>',
    'hexagon': '<polygon points="4.6,0 2.3,4 -2.3,4 -4.6,0 -2.3,-4 2.3,-4"/>',
    'plus': '<polygon points="-1.8,-5 1.8,-5 1.8,-1.8 5,-1.8 5,1.8 1.8,1.8 1.8,5 -1.8,5 -1.8,1.8 -5,1.8 -5,-1.8 -1.8,-1.8"/>',
    'star': '<polygon points="0,-5.5 1.6,-1.7 5.5,-1.7 2.4,0.8 3.5,4.8 0,2.4 -3.5,4.8 -2.4,0.8 -5.5,-1.7 -1.6,-1.7"/>'
  };
  function markSvg(band, size) {
    return '<svg width="' + size + '" height="' + size + '" viewBox="-7 -7 14 14"><g fill="' + band.color +
      '" stroke="#0e1217" stroke-width="1.6">' + (SHAPES[band.marker] || SHAPES.circle) + '</g></svg>';
  }
  function swatchSvg(band) {
    return '<svg width="44" height="14" viewBox="0 0 44 14" aria-hidden="true"><line x1="1" y1="7" x2="33" y2="7" stroke="' +
      band.color + '" stroke-width="2.5"' + (band.dash ? ' stroke-dasharray="' + band.dash + '"' : '') +
      '/><g transform="translate(37 7)" fill="' + band.color + '" stroke="#0e1217" stroke-width="1.6">' +
      (SHAPES[band.marker] || SHAPES.circle) + '</g></svg>';
  }

  // ------------------------------------------------------------------ map
  var map = L.map('map', {
    zoomControl: false, dragging: false, touchZoom: false, scrollWheelZoom: false,
    doubleClickZoom: false, boxZoom: false, keyboard: false, tap: false,
    zoomSnap: 1, inertia: false, fadeAnimation: false, zoomAnimation: !reduceMotion
  });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
  map.attributionControl.addAttribution('Natural Earth');
  var canvas = L.canvas({ padding: 0.6 });
  var zoomKey = 'area';
  map.setView([S[0].lat, S[0].lon], D.zooms[zoomKey]);

  // OpenStreetMap's tile servers refuse requests without a Referer, which a
  // page opened from a saved file never sends: they'd draw "Access blocked".
  var tiles = D.osm && location.protocol !== 'file:';
  if (tiles) {
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      opacity: D.osm.opacity, maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
  } else if (D.osm) {
    map.attributionControl.addAttribution('Street underlay shows when the movie is opened from a website');
  }
  var bg = D.background || {};
  function addGeoms(geoms, style) {
    (geoms || []).forEach(function (g) {
      L.geoJSON(g, { style: style, renderer: canvas, interactive: false }).addTo(map);
    });
  }
  var landFill = tiles ? 0.25 : 1;
  addGeoms(bg.land, { stroke: false, fillColor: '#1f242b', fillOpacity: landFill });
  addGeoms(bg.lakes, { color: '#3a4a5c', weight: 0.8, fillColor: '#0e1217', fillOpacity: landFill });
  addGeoms(bg.states, { color: '#363e49', weight: 0.8, dashArray: '3 3', fill: false });
  addGeoms(bg.borders, { color: '#4f5a68', weight: 1.2, fill: false });
  addGeoms(bg.coastline, { color: '#3e4a59', weight: 1, fill: false });

  // Where the operator has been: a faint dotted trail and a ring at each past stop.
  var trail = L.polyline([], { renderer: canvas, color: '#c3c2b7', weight: 1.5, opacity: 0.55,
    dashArray: '1 6', lineCap: 'round', interactive: false }).addTo(map);
  var stopRings = S.map(function (s, i) {
    var ring = L.circleMarker([s.lat, s.lon], { renderer: canvas, radius: 5, color: '#c3c2b7', weight: 1.5,
      fillColor: '#0e1217', fillOpacity: 1, opacity: 0.9 });
    var tip = document.createElement('div');
    var head = document.createElement('b'); head.textContent = s.grid; tip.appendChild(head);
    tip.appendChild(document.createTextNode(' · ' + s.n + ' QSO' + (s.n === 1 ? '' : 's') +
      ' · ' + utcSpan(s.utc0, s.utc1)));
    ring.bindTooltip(tip, { direction: 'top', className: 'stop-tip', offset: [0, -4] });
    return ring;
  });

  var station = L.marker([S[0].lat, S[0].lon], {
    icon: L.divIcon({ className: 'station', iconSize: [18, 18], iconAnchor: [9, 9],
      html: '<span class="ring"></span><svg width="18" height="18" viewBox="-9 -9 18 18">' +
        '<circle r="7" fill="#0e1217" stroke="#f0efec" stroke-width="2.4"/><circle r="3" fill="#f0efec"/></svg>' }),
    keyboard: false, interactive: false, zIndexOffset: 1000
  }).addTo(map);
  station.bindTooltip(S[0].grid, { permanent: true, direction: 'top', offset: [0, -11], className: 'station-tip' });

  // ------------------------------------------------------------------ contacts
  var visible = B.map(function () { return true; });
  var layers = [];             // per event: {line, mark, done}
  var shown = 0;               // events [0, shown) have layers on the map
  var flashes = [];
  var litStop = -1;            // stop whose lines are drawn bright (-1 while relocating)

  function lineStyle(e) {
    var b = B[e.band];
    var on = visible[e.band];
    return { color: b.color, weight: e.stop === litStop ? 2.2 : 1.6, dashArray: b.dash || null,
      opacity: on ? (e.stop === litStop ? 0.95 : 0.3) : 0 };
  }
  function markOpacity(e) { return visible[e.band] ? (e.stop === litStop ? 1 : 0.45) : 0; }

  function tipFor(e) {
    var b = B[e.band], s = S[e.stop];
    var el = document.createElement('div');
    var c = document.createElement('b'); c.textContent = e.call; el.appendChild(c);
    el.appendChild(document.createTextNode(' · ' + b.band + ' · ' + e.grid));
    el.appendChild(document.createElement('br'));
    el.appendChild(document.createTextNode(e.km + ' km from ' + s.grid + ' · ' + utcStamp(e.utc)));
    return el;
  }

  function addContact(i, animated) {
    var e = E[i], s = S[e.stop];
    var line = L.polyline([[s.lat, s.lon], animated ? [s.lat, s.lon] : [e.lat, e.lon]],
      Object.assign({ renderer: canvas, interactive: false, lineCap: 'round' }, lineStyle(e))).addTo(map);
    var mark = L.marker([e.lat, e.lon], {
      icon: L.divIcon({ className: 'qso-mark', html: markSvg(B[e.band], 12), iconSize: [12, 12], iconAnchor: [6, 6] }),
      keyboard: false, opacity: animated ? 0 : markOpacity(e)
    }).addTo(map);
    mark.bindTooltip(tipFor(e), { direction: 'top', offset: [0, -6], className: 'qso-tip' });
    layers[i] = { line: line, mark: mark, done: !animated };
    if (animated && !reduceMotion) flash(e);
  }

  function removeContact(i) {
    var l = layers[i];
    if (!l) return;
    map.removeLayer(l.line); map.removeLayer(l.mark);
    layers[i] = null;
  }

  function flash(e) {
    var f = L.marker([e.lat, e.lon], { interactive: false, keyboard: false,
      icon: L.divIcon({ className: '', iconSize: [0, 0], iconAnchor: [-8, 18],
        html: '<div class="call-flash"></div>' }) }).addTo(map);
    f.getElement().firstChild.textContent = e.call;
    flashes.push(f);
    while (flashes.length > MAX_FLASHES) map.removeLayer(flashes.shift());
    setTimeout(function () {
      var k = flashes.indexOf(f);
      if (k >= 0) { flashes.splice(k, 1); map.removeLayer(f); }
    }, 2300);
  }
  function clearFlashes() { flashes.forEach(function (f) { map.removeLayer(f); }); flashes = []; }

  function restyleAll() {
    for (var i = 0; i < shown; i++) {
      var l = layers[i]; if (!l) continue;
      l.line.setStyle(lineStyle(E[i]));
      if (l.done) l.mark.setOpacity(markOpacity(E[i]));
    }
  }

  // ------------------------------------------------------------------ prefix counts
  var calls = [0], seen = {}, bandCounts = B.map(function () { return [0]; });
  E.forEach(function (e, i) {
    if (!seen[e.call]) { seen[e.call] = 1; calls.push(calls[i] + 1); } else calls.push(calls[i]);
    B.forEach(function (b, k) { bandCounts[k].push(bandCounts[k][i] + (e.band === k ? 1 : 0)); });
  });

  // ------------------------------------------------------------------ timeline lookups
  function countAt(T) {             // number of contacts made by time T
    var lo = 0, hi = E.length;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (E[mid].t <= T) lo = mid + 1; else hi = mid; }
    return lo;
  }
  function clockAt(T) {
    var C = D.clock;
    if (T <= C[0][0]) return C[0][1];
    for (var i = 1; i < C.length; i++) {
      if (T <= C[i][0]) {
        var f = (T - C[i - 1][0]) / Math.max(1e-6, C[i][0] - C[i - 1][0]);
        return lerp(C[i - 1][1], C[i][1], f);
      }
    }
    return C[C.length - 1][1];
  }
  // Station position, its current stop, and the relocation in progress (if any).
  function stationAt(T) {
    var st = { lat: S[0].lat, lon: S[0].lon, stop: 0, move: null };
    for (var i = 0; i < D.moves.length; i++) {
      var m = D.moves[i];
      if (T < m.t0) break;
      var a = S[m.from], b = S[m.to];
      if (T < m.t1 && !reduceMotion) {
        var f = ease((T - m.t0) / Math.max(1e-6, m.t1 - m.t0));
        st = { lat: lerp(a.lat, b.lat, f), lon: lerp(a.lon, b.lon, f), stop: m.from, move: m };
      } else {
        st = { lat: b.lat, lon: b.lon, stop: m.to, move: T < m.t1 ? m : null };
      }
    }
    return st;
  }
  function segmentAt(T) {
    for (var i = 0; i < D.segments.length; i++) {
      var g = D.segments[i];
      if (T >= g.t0 && T < g.t1) return g;
    }
    return null;
  }
  function dayAt(T) {
    var d = 0;
    for (var i = 0; i < D.days.length; i++) if (D.days[i].t - 0.9 <= T) d = i;
    return d;
  }
  function hoursMin(minutes) {
    if (minutes >= 2880) return Math.round(minutes / 1440) + ' days';
    var h = Math.floor(minutes / 60), m = minutes % 60;
    return h ? h + ' h ' + (m ? m + ' min' : '') : m + ' min';
  }

  // ------------------------------------------------------------------ render
  var T = 0, playing = false, speed = 1, last = 0, started = false;
  var lastStop = -1, lastDay = -1, center = null;

  function render(seek) {
    var n = countAt(T);
    if (seek || n < shown) {
      clearFlashes();
      for (var i = n; i < shown; i++) removeContact(i);
      for (var j = Math.min(n, shown); j < n; j++) if (!layers[j]) addContact(j, false);
      for (var k = 0; k < n; k++) if (layers[k] && !layers[k].done) finish(k);
    } else {
      for (var a = shown; a < n; a++) addContact(a, true);
    }
    shown = n;

    // Lines still drawing in.
    for (var g = Math.max(0, n - 16); g < n; g++) {
      var l = layers[g];
      if (!l || l.done) continue;
      var f = reduceMotion ? 1 : Math.min(1, (T - E[g].t) / GROW);
      var e = E[g], s = S[e.stop];
      if (f >= 1) finish(g);
      else l.line.setLatLngs([[s.lat, s.lon], [lerp(s.lat, e.lat, ease(f)), lerp(s.lon, e.lon, ease(f))]]);
    }

    // Station, camera and trail.
    var st = stationAt(T);
    var lit = st.move ? -1 : st.stop;
    if (lit !== litStop) { litStop = lit; restyleAll(); }
    station.setLatLng([st.lat, st.lon]);
    if (!center || Math.abs(center[0] - st.lat) > 1e-7 || Math.abs(center[1] - st.lon) > 1e-7) {
      center = [st.lat, st.lon];
      map.setView(center, map.getZoom(), { animate: false });
    }
    var path = [];
    var reached = st.move ? st.move.from : st.stop;
    for (var p = 0; p <= reached; p++) path.push([S[p].lat, S[p].lon]);
    if (st.move) path.push([st.lat, st.lon]);
    trail.setLatLngs(path);
    stopRings.forEach(function (r, idx) {
      var want = idx < reached || (st.move && idx === st.move.from);
      if (want && !map.hasLayer(r)) r.addTo(map);
      if (!want && map.hasLayer(r)) map.removeLayer(r);
    });

    // HUD.
    var day = dayAt(T), dd = D.days[day];
    text('day', 'Day ' + (day + 1) + ' of ' + D.days.length + ' · ' + dd.label +
      (D.totals.weekends > 1 ? ' · Weekend ' + dd.weekend : ''));
    var nowMin = Math.round(clockAt(T));
    text('clock', utcLabel(nowMin));
    text('clock-date', 'UTC · ' + utcDate(nowMin));
    text('n-qsos', String(n));
    text('n-calls', String(calls[n]));
    text('n-stop', (st.stop + 1) + '/' + S.length);
    if (D.scoring) text('score', (n ? E[n - 1].sc : 0).toLocaleString('en-US'));
    var whereEl = $('where');
    var whereText = st.move ? 'Relocating ' + S[st.move.from].grid + ' → ' + S[st.move.to].grid
                            : 'Operating from ' + S[st.stop].grid;
    if (whereEl.dataset.v !== whereText) {
      whereEl.dataset.v = whereText;
      whereEl.textContent = '';
      var parts = whereText.split(/([A-R]{2}\d{2}[A-X]{0,2})/i);
      parts.forEach(function (part, idx) {
        if (idx % 2) { var bb = document.createElement('b'); bb.textContent = part; whereEl.appendChild(bb); }
        else whereEl.appendChild(document.createTextNode(part));
      });
    }
    if (!st.move && station.getTooltip().getContent() !== S[st.stop].grid) station.setTooltipContent(S[st.stop].grid);
    B.forEach(function (b, k) { text('count-' + k, String(bandCounts[k][n])); });

    // Fast-forward banner and day cards.
    var seg = segmentAt(T), bannerText = '';
    if (seg && started) {
      if (seg.kind === 'idle') bannerText = '⏩ Fast-forwarding ' + hoursMin(seg.minutes) + ' at ' + S[seg.from].grid;
      else bannerText = '⏩ ' + hoursMin(seg.minutes) + ' later · ' + S[seg.from].grid + ' → ' +
        S[seg.to].grid + ' (' + seg.km + ' km)';
    }
    var banner = $('banner');
    banner.hidden = !bannerText;
    text('banner', bannerText);
    var card = $('card'), showCard = false;
    if (started) {
      for (var c = 0; c < D.days.length; c++) {
        var dt = D.days[c].t;
        if (T >= dt - 1.0 && T < dt + 1.3) {
          showCard = true;
          var newWeekend = c === 0 || D.days[c].weekend !== D.days[c - 1].weekend;
          text('card-k', newWeekend && D.totals.weekends > 1 ? 'Weekend ' + D.days[c].weekend + ' · Day ' + (c + 1)
                                                            : 'Day ' + (c + 1));
          text('card-v', D.days[c].label);
        }
      }
    }
    card.hidden = !showCard;
    if (st.stop !== lastStop || day !== lastDay) {
      if (started) $('live').textContent = dd.label + ', operating from ' + S[st.stop].grid + '.';
      lastStop = st.stop; lastDay = day;
    }

    $('scrub').value = String(Math.round(T / DUR * 1000));
    text('remain', mmss((DUR - T) / speed));
  }

  function finish(i) {
    var l = layers[i], e = E[i];
    l.line.setLatLngs([[S[e.stop].lat, S[e.stop].lon], [e.lat, e.lon]]);
    l.mark.setOpacity(markOpacity(e));
    l.done = true;
  }

  // ------------------------------------------------------------------ playback
  function tick(now) {
    if (playing) {
      T = Math.min(DUR, T + Math.min(0.1, (now - last) / 1000) * speed);
      last = now;
      render(false);
      if (T >= DUR) setPlaying(false);
    }
    requestAnimationFrame(tick);
  }
  function setPlaying(on) {
    if (on && T >= DUR) { T = 0; render(true); }
    playing = on;
    last = performance.now();
    var btn = $('play');
    btn.innerHTML = on ? '&#10074;&#10074;' : '&#9654;';
    btn.setAttribute('aria-label', on ? 'Pause' : 'Play');
  }
  function begin() {
    if (!started) { started = true; $('start').hidden = true; }
    setPlaying(true);
  }

  $('start-btn').addEventListener('click', begin);
  $('play').addEventListener('click', function () { if (!started) begin(); else setPlaying(!playing); });
  $('restart').addEventListener('click', function () { T = 0; render(true); if (!started) begin(); else setPlaying(true); });
  Array.prototype.forEach.call(document.querySelectorAll('[data-speed]'), function (btn) {
    btn.addEventListener('click', function () {
      speed = parseFloat(btn.dataset.speed);
      document.querySelectorAll('[data-speed]').forEach(function (b) { b.setAttribute('aria-pressed', String(b === btn)); });
      render(false);
    });
  });
  function setZoomKey(key) {
    zoomKey = key;
    document.querySelectorAll('[data-zoom]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.zoom === key)); });
    map.setZoom(D.zooms[key], { animate: !playing && !reduceMotion });
  }
  document.querySelectorAll('[data-zoom]').forEach(function (b) {
    b.addEventListener('click', function () { setZoomKey(b.dataset.zoom); });
  });
  var scrub = $('scrub');
  scrub.addEventListener('input', function () {
    if (!started) { started = true; $('start').hidden = true; }
    T = parseInt(scrub.value, 10) / 1000 * DUR;
    render(true);
  });
  document.addEventListener('keydown', function (ev) {
    var tag = (ev.target && ev.target.tagName) || '';
    if (ev.key === ' ' && tag !== 'BUTTON' && tag !== 'INPUT') {
      ev.preventDefault();
      if (!started) begin(); else setPlaying(!playing);
    }
  });

  // Legend: band toggles with live counts.
  var rows = $('legend-rows');
  B.forEach(function (b, k) {
    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'leg'; btn.setAttribute('aria-pressed', 'true');
    btn.innerHTML = swatchSvg(b) + '<span class="name"></span><span class="count" id="count-' + k + '">0</span>';
    btn.querySelector('.name').textContent = b.band;
    btn.title = b.band + ': ' + b.n + ' QSO' + (b.n === 1 ? '' : 's') + ' in the log';
    btn.addEventListener('click', function () {
      visible[k] = !visible[k];
      btn.setAttribute('aria-pressed', String(visible[k]));
      restyleAll();
    });
    rows.appendChild(btn);
  });

  // Timeline ticks at the start of each day.
  var ticks = $('ticks');
  D.days.forEach(function (d, i) {
    var t = document.createElement('span');
    t.style.left = (d.t / DUR * 100) + '%';
    t.textContent = (i === 0 || d.weekend !== D.days[i - 1].weekend) ? d.label : d.label.split(' ')[0];
    ticks.appendChild(t);
  });

  // Table view.
  var table = $('table');
  var head = document.createElement('tr');
  ['#', 'Grid', 'Operating day', 'UTC', 'QSOs'].concat(B.map(function (b) { return b.band; })).forEach(function (h) {
    var th = document.createElement('th'); th.textContent = h; head.appendChild(th);
  });
  table.appendChild(head);
  S.forEach(function (s, i) {
    var tr = document.createElement('tr');
    [String(i + 1), s.grid, D.days[s.day0].label, utcSpan(s.utc0, s.utc1), String(s.n)]
      .concat(s.counts.map(function (c) { return c ? String(c) : '–'; }))
      .forEach(function (v) { var td = document.createElement('td'); td.textContent = v; tr.appendChild(td); });
    table.appendChild(tr);
  });
  function toggleTable(open) {
    $('table-panel').hidden = !open;
    $('table-btn').setAttribute('aria-expanded', String(open));
  }
  $('table-btn').addEventListener('click', function () { toggleTable($('table-panel').hidden); });
  $('table-close').addEventListener('click', function () { toggleTable(false); });

  // Titles.
  text('who', D.title);
  $('score-line').hidden = !D.scoring;
  text('start-title', D.title);
  text('start-sub', D.totals.qsos + ' QSOs · ' + D.totals.calls + ' stations · ' + D.totals.stops +
    ' operating location' + (D.totals.stops === 1 ? '' : 's') + ' · ' + D.totals.days + ' day' +
    (D.totals.days === 1 ? '' : 's'));
  setZoomKey('area');
  window.addEventListener('resize', function () { map.invalidateSize(false); center = null; render(false); });

  // For automated checks and embedding: jump to a moment and report state.
  window.contestMovie = {
    seek: function (t) { if (!started) { started = true; $('start').hidden = true; } T = Math.max(0, Math.min(DUR, t)); render(true); },
    play: begin, pause: function () { setPlaying(false); },
    state: function () { return { t: T, duration: DUR, shown: shown, stop: stationAt(T).stop, zoom: map.getZoom(), playing: playing }; }
  };
  render(true);
  requestAnimationFrame(tick);
})();
</script>
</body>
</html>
"""


# Contest names as Cabrillo writes them -> how the --animate title shows them.
_CONTEST_TITLES = {
    'ARRL-10-GHZ': 'ARRL 10 GHz and Up',
}


def _read_cabrillo_header(filename, key):
    """Value of one Cabrillo header line (e.g. CONTEST), or None."""
    try:
        with open(filename, 'r') as f:
            for line in f:
                if line.upper().startswith(key.upper() + ':'):
                    return line.split(':', 1)[1].strip() or None
                if line.startswith('QSO:'):
                    break
    except OSError:
        pass
    return None


def _round_coords(obj, places=4):
    """GeoJSON geometry with coordinates rounded (about 10 m), to keep the page small."""
    if isinstance(obj, dict):
        return {k: _round_coords(v, places) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_coords(v, places) for v in obj]
    if isinstance(obj, float):
        return round(obj, places)
    return obj


def _animation_background(lat_min, lat_max, lon_min, lon_max, tolerance):
    """Natural Earth 10m land, lakes, coastline, country and state/province
    borders for the --animate map. The movie zooms in further than the static
    maps, so it needs the detailed scale; it falls back to the coarse layers
    the --html maps use if the detailed data can't be read."""
    layers = {
        'land': ('physical', 'land'),
        'lakes': ('physical', 'lakes'),
        'coastline': ('physical', 'coastline'),
        'borders': ('cultural', 'admin_0_boundary_lines_land'),
        'states': ('cultural', 'admin_1_states_provinces_lines'),
    }
    out = {}
    for key, (category, name) in layers.items():
        feature = cfeature.NaturalEarthFeature(category, name, '10m')
        out[key] = _round_coords(_geojson_geoms_for_bbox(feature, lon_min, lon_max, lat_min, lat_max,
                                                         tolerance=tolerance))
    if not out['land'] and not out['coastline']:
        coarse = _background_geojson(lon_min, lon_max, lat_min, lat_max)
        for key in ('land', 'lakes', 'coastline', 'borders'):
            out[key] = _round_coords(coarse.get(key, []))
    return out


_LEAFLET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor', 'leaflet')


def _leaflet_tags():
    """Leaflet's CSS and JS for the --animate page: inlined from the bundled
    copy in vendor/leaflet so a saved movie needs no CDN, or the unpkg tags if
    that copy isn't there (e.g. a pip install, which packages only this file)."""
    try:
        with open(os.path.join(_LEAFLET_DIR, 'leaflet.css'), encoding='utf-8') as f:
            css = f.read()
        with open(os.path.join(_LEAFLET_DIR, 'leaflet.js'), encoding='utf-8') as f:
            js = f.read()
    except OSError:
        return ('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">',
                '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>')
    return '<style>\n' + css + '\n</style>', '<script>\n' + js.replace('</script', '<\\/script') + '\n</script>'


def create_animation_html(qso_records_by_band, callsign, fallback_home_grid=None, output_file=None,
                          contest=None, target_seconds=None, osm_basemap=False, osm_opacity=0.3):
    """Write the --animate page: the whole log replayed as a movie, following
    the station between operating locations with each contact drawn in as it
    was made, colored by band. Returns the file name, or None if the log has
    nothing to animate (no contacts with a time and both grids)."""
    data = build_animation_data(qso_records_by_band, fallback_home_grid, target_seconds, contest=contest)
    if data is None:
        print("Skipping --animate: no contacts with a date, time, worked grid and operating grid")
        return None

    # Background: everything the camera can show, with room for the widest
    # zoom level around the outermost stations.
    (lat_min, lon_min), (lat_max, lon_max) = data['bounds']
    mid_lat = (lat_min + lat_max) / 2
    meters_per_px = 156543.03 * math.cos(math.radians(mid_lat)) / (2 ** data['zooms']['region'])
    pad_km = 700 * meters_per_px / 1000.0
    pad_lat = min(10.0, pad_km / 111.0)
    pad_lon = min(14.0, pad_km / (111.0 * max(0.2, math.cos(math.radians(mid_lat)))))
    data['background'] = _animation_background(lat_min - pad_lat, lat_max + pad_lat,
                                               lon_min - pad_lon, lon_max + pad_lon, tolerance=0.006)
    data['osm'] = {'opacity': osm_opacity} if osm_basemap else None

    year = datetime(1970, 1, 1) + timedelta(minutes=data['events'][0]['utc'])
    contest_title = _CONTEST_TITLES.get((contest or '').upper(), (contest or '').replace('-', ' ').strip())
    data['title'] = f"{callsign} · {contest_title} {year.year}" if contest_title else f"{callsign} · {year.year}"

    if output_file is None:
        output_file = f"{callsign}_contest_animation.html"
    # Escape <, > and & so nothing in the log (a callsign, say) can end the script block.
    data_json = (json.dumps(data, separators=(',', ':'))
                 .replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026'))
    leaflet_css, leaflet_js = _leaflet_tags()
    page = _ANIMATION_PAGE_TEMPLATE.replace('__TITLE__', _esc(f"{callsign} contest movie"))
    page = page.replace('__LEAFLET_CSS__', leaflet_css).replace('__LEAFLET_JS__', leaflet_js)
    page = page.replace('__DATA_JSON__', data_json)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(page)
    totals = data['totals']
    print(f"Animation saved as {output_file} ({totals['qsos']} contacts, {totals['stops']} operating "
          f"location{'s' if totals['stops'] != 1 else ''}, {data['duration']:.0f} s at 1x)")
    if totals['skipped']:
        print(f"  {totals['skipped']} contact(s) left out: missing a date/time or a grid square")
    return output_file


def main():
    """Main entry point for console script"""
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate Maidenhead grid square maps from contest logs')
    parser.add_argument('filename', help='Contest log file (.cbr for Cabrillo, .csv for CSV)')
    parser.add_argument('--continents', nargs='+',
                       choices=['north_america', 'south_america', 'europe', 'africa', 'asia', 'oceania'],
                       help='Continents to include (auto-detected if not specified)')
    parser.add_argument('--paths', action='store_true',
                       help='Also generate a local-area "path map" per band/location, showing a line '
                            'from your home grid to each worked station (great for small-area contests '
                            'like ARRL 10 GHz and Up). Requires a home grid, either auto-detected '
                            'from the log or supplied with --home-grid.')
    parser.add_argument('--home-grid', metavar='GRID',
                       help='Your own 4- or 6-character Maidenhead grid square. Overrides auto-detection '
                            'and forces every contact to be treated as worked from this one location '
                            '(disables automatic per-location splitting for multi-location/rover logs).')
    parser.add_argument('--osm-basemap', action='store_true',
                       help='Add a faint OpenStreetMap tile basemap underneath every map generated for '
                            '902 MHz and higher bands (33cm and up). All existing layers are kept as-is; '
                            'this just adds OSM imagery beneath them. Requires internet access at run '
                            'time and fails quietly (map still generates) if the tiles can\'t be fetched.')
    parser.add_argument('--osm-opacity', type=float, default=0.3, metavar='0.0-1.0',
                       help='Opacity of the --osm-basemap underlay (default: 0.3, i.e. 30%%).')
    parser.add_argument('--html', action='store_true',
                       help='Also generate an interactive HTML version of every map produced in this '
                            'run (density maps, and path maps when --paths is used), suitable for '
                            'embedding on a website. Grid squares / worked-station points become '
                            'hoverable (desktop) or tappable (mobile) with a popup showing that '
                            'contact\'s callsign, date, six-character grid, and other QSO details. '
                            'Looks the same as the PNG output, with or without --osm-basemap.')

    parser.add_argument('--animate', action='store_true',
                       help='Also generate an animated HTML "movie" of the whole log: the map follows '
                            'you from one operating location to the next while a line to each worked '
                            'station is drawn in as the contact was made, colored by band, with '
                            '0.5x/1x/2x playback, three fixed zoom levels, a timeline and a summary '
                            'table. All operating days (e.g. both weekends of the ARRL 10 GHz and Up '
                            'contest) play back to back; long breaks and drives are fast-forwarded.')
    parser.add_argument('--animate-only', action='store_true',
                       help='Generate only the --animate movie, without the per-band maps.')
    parser.add_argument('--animate-seconds', type=float, metavar='SECONDS',
                       help='Length of the --animate movie at 1x speed (default: about 2 minutes '
                            'for a busy multi-location log, less for a small one).')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    filename = args.filename

    if not 0.0 <= args.osm_opacity <= 1.0:
        print(f"--osm-opacity must be between 0.0 and 1.0 (got {args.osm_opacity}); using default 0.3")
        args.osm_opacity = 0.3

    # Determine file format based on extension
    if filename.lower().endswith('.csv'):
        grids_by_band, callsign, detected_home_grid, contacts_by_band, qso_records_by_band = parse_csv_grids(filename)
        print(f"Parsed CSV file: {filename}")
    elif filename.lower().endswith(('.cbr', '.log')):
        grids_by_band, callsign, detected_home_grid, contacts_by_band, qso_records_by_band = parse_cabrillo_grids(filename)
        print(f"Parsed Cabrillo file: {filename}")
    else:
        print("Unsupported file format. Use .csv, .cbr, or .log files.")
        sys.exit(1)

    if not grids_by_band:
        print("No Maidenhead grid squares found in file")
        return

    def records_for_location(band, loc):
        """This band's QSO detail records, restricted to one operating
        location using the same (qso_home_grid or detected_home_grid) rule
        used to group contacts_by_band. loc=None means "no restriction"
        (used when --home-grid merges every location into a single map)."""
        records = qso_records_by_band.get(band, [])
        if loc is None:
            return records
        return [r for r in records if (r.get('home_grid') or detected_home_grid) == loc]

    # A manual --home-grid always wins, and forces single-location treatment
    # even if the log actually has multiple operating locations.
    override_home_grid = None
    if args.home_grid:
        candidate = args.home_grid.strip().upper()
        if is_valid_grid(candidate):
            override_home_grid = candidate
        else:
            print(f"--home-grid '{args.home_grid}' is not a valid 4- or 6-character grid square")

    # Detect whether this log covers more than one operating location overall
    # (e.g. a rover moving between grids during the contest).
    all_locations = set()
    for contacts in contacts_by_band.values():
        for _, _, qso_home_grid in contacts:
            all_locations.add(qso_home_grid or detected_home_grid)
    if override_home_grid and len(all_locations) > 1:
        print(f"Note: {len(all_locations)} operating locations detected in this log, but --home-grid "
              f"overrides per-location detection; treating all contacts as worked from {override_home_grid}.")

    if args.animate or args.animate_only:
        animate_records = qso_records_by_band
        if override_home_grid:
            animate_records = {band: [dict(r, home_grid=override_home_grid) for r in records]
                               for band, records in qso_records_by_band.items()}
        contest = None if filename.lower().endswith('.csv') else _read_cabrillo_header(filename, 'CONTEST')
        create_animation_html(animate_records, callsign, override_home_grid or detected_home_grid,
                              contest=contest, target_seconds=args.animate_seconds,
                              osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
        if args.animate_only:
            return

    for band, grids in grids_by_band.items():
        contacts = contacts_by_band.get(band)

        if not contacts or override_home_grid:
            # No per-contact location data available for this band (e.g. a CSV
            # without a recognized grid column), or the user forced a single
            # location with --home-grid: one map for the whole band, as before.
            create_grid_map(grids, callsign, band, args.continents,
                             osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
            if args.html:
                create_grid_map_html(grids, records_for_location(band, None), callsign, band,
                                      args.continents, osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
            if args.paths:
                home_grid = override_home_grid or detected_home_grid
                if home_grid and detected_home_grid and not override_home_grid:
                    print(f"Auto-detected home grid for {band}: {home_grid}")
                if not home_grid:
                    print(f"Skipping --paths map for {band}: no home grid available. "
                          "Pass --home-grid GRIDSQUARE or use a log that reports your own grid per QSO.")
                elif contacts:
                    create_path_map(home_grid, [(c, g) for c, g, _ in contacts], callsign, band,
                                     osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
                    if args.html:
                        create_path_map_html(home_grid, [(c, g) for c, g, _ in contacts],
                                              records_for_location(band, None), callsign, band,
                                              osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
                else:
                    print(f"Skipping --paths map for {band}: no worked-station contacts were parsed")
            continue

        # Group this band's contacts by the operating location reported on
        # each QSO, falling back to the file's overall detected home grid.
        location_groups = defaultdict(list)
        for their_call, their_grid, qso_home_grid in contacts:
            loc = qso_home_grid or detected_home_grid
            location_groups[loc].append((their_call, their_grid))

        if len(location_groups) <= 1:
            # Only one location in play for this band - keep the simple,
            # un-suffixed output rather than adding a needless location tag.
            create_grid_map(grids, callsign, band, args.continents,
                             osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
            home_grid = next(iter(location_groups), None)
            if args.html:
                create_grid_map_html(grids, records_for_location(band, home_grid), callsign, band,
                                      args.continents, osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
            if args.paths:
                if not home_grid:
                    print(f"Skipping --paths map for {band}: no home grid available. "
                          "Pass --home-grid GRIDSQUARE or use a log that reports your own grid per QSO.")
                else:
                    create_path_map(home_grid, location_groups[home_grid], callsign, band,
                                     osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
                    if args.html:
                        create_path_map_html(home_grid, location_groups[home_grid],
                                              records_for_location(band, home_grid), callsign, band,
                                              osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
            continue

        # Multiple operating locations for this band: one graphic per location.
        location_labels = ', '.join(loc or 'Unknown' for loc in location_groups)
        print(f"{band}: {len(location_groups)} operating locations detected ({location_labels}); "
              f"generating a separate map per location")
        for loc, loc_contacts in location_groups.items():
            if not loc:
                print(f"Skipping {band} group with no identifiable home grid ({len(loc_contacts)} contacts)")
                continue
            loc_grids = [g for _, g in loc_contacts]
            create_grid_map(loc_grids, callsign, band, args.continents, location=loc,
                             osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
            if args.html:
                create_grid_map_html(loc_grids, records_for_location(band, loc), callsign, band,
                                      args.continents, location=loc,
                                      osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
            if args.paths:
                create_path_map(loc, loc_contacts, callsign, band,
                                 osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)
                if args.html:
                    create_path_map_html(loc, loc_contacts, records_for_location(band, loc), callsign, band,
                                          osm_basemap=args.osm_basemap, osm_opacity=args.osm_opacity)

if __name__ == "__main__":
    main()
