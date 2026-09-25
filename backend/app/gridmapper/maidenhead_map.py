#!/usr/bin/env python3
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
    '2mm': 142000.0, '1mm': 241000.0,
    '10G': 10000.0, '24G': 24000.0, '47G': 47000.0, '75G': 75000.0, '123G': 123000.0,
}

OSM_MIN_FREQ_MHZ = 902.0  # 902 MHz and up, per the --osm-basemap band cutoff

def band_is_osm_eligible(band):
    """Best-effort check for whether a band label represents 902 MHz or
    higher. Known band names (see BAND_MIN_FREQ_MHZ) are looked up directly;
    unrecognized labels fall back to parsing a trailing numeric value with an
    optional GHz/MHz/kHz suffix (e.g. "915MHz", "3.4GHz"). Returns False
    (conservatively, no basemap) if the band can't be identified either way.
    """
    if band in BAND_MIN_FREQ_MHZ:
        return BAND_MIN_FREQ_MHZ[band] >= OSM_MIN_FREQ_MHZ

    match = re.match(r'^([\d.]+)\s*(GHz|MHz|kHz)?$', band.strip(), re.IGNORECASE)
    if not match:
        return False

    value = float(match.group(1))
    unit = (match.group(2) or 'MHz').lower()
    if unit == 'ghz':
        value *= 1000
    elif unit == 'khz':
        value /= 1000
    return value >= OSM_MIN_FREQ_MHZ

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
        elif 142000000 <= freq <= 149000000:
            return "2mm"
        elif 241000000 <= freq <= 250000000:
            return "1mm"
        
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

def _geojson_geoms_for_bbox(feature, lon_min, lon_max, lat_min, lat_max):
    """Extract a cartopy Feature's geometries as plain GeoJSON geometry
    dicts, clipped and simplified to the given lon/lat bounding box so the
    embedded --html output stays a reasonable size. Returns [] (background
    for that layer simply omitted) if the geometry can't be read."""
    geoms = []
    try:
        bbox_poly = shapely_box(lon_min, lat_min, lon_max, lat_max)
        span = max(lon_max - lon_min, 0.01)
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

  if (DATA.osm) {
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
    vhf_uhf_bands = ['6m', '2m', '1.25m', '70cm', '33cm', '23cm', '13cm', '9cm', '6cm', '3cm', 
                     '1.25cm', '6mm', '4mm', '2.5mm', '2mm', '1mm', '10G', '24G', '47G', '75G', '123G']
    if band in vhf_uhf_bands or 'GHz' in band:
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

    vhf_uhf_bands = ['6m', '2m', '1.25m', '70cm', '33cm', '23cm', '13cm', '9cm', '6cm', '3cm',
                      '1.25cm', '6mm', '4mm', '2.5mm', '2mm', '1mm', '10G', '24G', '47G', '75G', '123G']
    grid_outlines = []
    if band in vhf_uhf_bands or 'GHz' in band:
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
