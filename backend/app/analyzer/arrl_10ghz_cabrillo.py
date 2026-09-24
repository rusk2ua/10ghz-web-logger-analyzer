#!/usr/bin/env python3

from datetime import datetime
import math

from data_source import (
    load_source_or_exit, build_arg_parser,
    normalize_band, band_multiplier, band_to_cabrillo, BAND_ORDER,
)

VERSION = "1.5.3"

def grid_to_latlon(grid):
    """Convert 6-digit Maidenhead grid to lat/lon"""
    grid = str(grid).upper().strip()
    if len(grid) < 4:
        return 0.0, 0.0  # Return origin for invalid grids
    if len(grid) < 6:
        grid = grid + 'AA'[:6-len(grid)]  # Pad with AA if too short
    grid = grid[:6]  # Truncate if too long
    
    try:
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90
        lon += (ord(grid[2]) - ord('0')) * 2
        lat += (ord(grid[3]) - ord('0')) * 1
        lon += (ord(grid[4]) - ord('A')) * 5/60
        lat += (ord(grid[5]) - ord('A')) * 2.5/60
        return lat + 1.25/60, lon + 2.5/60
    except (ValueError, IndexError):
        return 0.0, 0.0  # Return origin for invalid grids

def calculate_distance(grid1, grid2):
    """Calculate distance between two grids in km"""
    lat1, lon1 = grid_to_latlon(grid1)
    lat2, lon2 = grid_to_latlon(grid2)
    
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return 6371 * 2 * math.asin(math.sqrt(a))

def get_contest_year(df):
    """Determine the contest year from the QSO dates in df (the most common
    year among them), falling back to the current year if none parse."""
    from collections import Counter
    years = []
    for date_str in df['date'].dropna().unique():
        date_str = str(date_str)
        try:
            if '/' in date_str:
                date_obj = datetime.strptime(date_str, '%m/%d/%Y')
            else:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            years.append(date_obj.year)
        except ValueError:
            continue
    if not years:
        return datetime.now().year
    return Counter(years).most_common(1)[0][0]

def generate_summary(df, header_info, total_score):
    """Generate plain-text summary matching the screenshot format"""
    lines = []

    # Header
    lines.append(f"ARRL 10 GHz and Up Contest, {get_contest_year(df)}")
    lines.append(f"Call\t\t{header_info['callsign']}")
    lines.append(f"Class\t\t{header_info['category_operator'].replace('-', ' ').title()}")
    lines.append(f"Score\t\t{total_score}")
    lines.append("")
    lines.append("Band\t\tQSOs\t\tPoints\t\tUnique Calls\tBest DX (km)")
    
    # Calculate stats per band
    band_stats = {}
    for _, row in df.iterrows():
        band = normalize_band(row['band'])  # Normalize band name
        call = row['call'].upper()
        distance = calculate_distance(row['sourcegrid'], row['grid'])
        distance_km = max(1, math.ceil(distance))
        multiplier = band_multiplier(band)
        points = distance_km * multiplier
        
        if band not in band_stats:
            band_stats[band] = {
                'qsos': 0,
                'points': 0,
                'unique_calls': set(),
                'best_dx': 0
            }
        
        band_stats[band]['qsos'] += 1
        band_stats[band]['points'] += points
        band_stats[band]['unique_calls'].add(call)
        band_stats[band]['best_dx'] = max(band_stats[band]['best_dx'], math.ceil(distance))
    
    # Sort bands by frequency (band_stats keys are already canonical display
    # names, e.g. "78 GHz", via normalize_band -- so this is a direct lookup,
    # not a substring match against band_order)
    total_qsos = 0
    total_points = 0
    total_unique = set()

    for band_name in BAND_ORDER:
        if band_name in band_stats:
            stats = band_stats[band_name]
            qsos = stats['qsos']
            distance_points = stats['points']
            unique_calls = len(stats['unique_calls'])
            bonus_points = unique_calls * 100
            total_band_points = distance_points + bonus_points
            best_dx = stats['best_dx']
            
            lines.append(f"{band_name}\t\t{qsos}\t\t{total_band_points}\t\t{unique_calls}\t\t{best_dx}")
            
            total_qsos += qsos
            total_points += total_band_points
            total_unique.update(stats['unique_calls'])
        else:
            lines.append(f"{band_name}\t\t0\t\t0\t\t0\t\t0")
    
    # Calculate totals
    total_qsos = sum(stats['qsos'] for stats in band_stats.values())
    total_points = sum(stats['points'] + len(stats['unique_calls']) * 100 for stats in band_stats.values())
    total_unique_sum = sum(len(stats['unique_calls']) for stats in band_stats.values())
    
    lines.append("")
    lines.append("Light")
    lines.append(f"Total\t\t{len(df)}\t\t{total_score}\t\t{total_unique_sum}")
    
    return '\n'.join(lines)

def check_duplicates(df):
    """Check for duplicate contacts (same call, source grid, destination grid, and band)"""
    print("\n=== Duplicate Contact Check ===")
    
    # Normalize band names for comparison
    df['band_normalized'] = df['band'].apply(lambda x: normalize_band(x))
    
    # Create a key for duplicate detection: call + source grid + destination grid + band
    df['dup_key'] = df['call'].str.upper() + '_' + df['sourcegrid'].str.upper() + '_' + df['grid'].str.upper() + '_' + df['band_normalized']
    
    # Find duplicates
    duplicates = df[df.duplicated(subset=['dup_key'], keep=False)]
    
    if len(duplicates) > 0:
        print(f"WARNING: Found {len(duplicates)} duplicate contacts:")
        print("Date     Time Call     Band    Source   Dest     Status")
        print("-" * 55)
        
        # Group by duplicate key to show sets
        for dup_key, group in duplicates.groupby('dup_key'):
            for i, (_, row) in enumerate(group.iterrows()):
                status = "ORIGINAL" if i == 0 else "DUPLICATE"
                print(f"{str(row['date'])[:10]} {str(row['time']).zfill(4)} {str(row['call']).upper():<8} {str(row['band']):<7} {str(row['sourcegrid']).upper():<8} {str(row['grid']).upper():<8} {status}")
            print("-" * 55)
    else:
        print("No duplicate contacts found.")
    
    print("")
    return len(duplicates)

def calculate_score(df):
    """Calculate contest score - distance points × band multiplier + 100 per unique call per band"""
    
    # Check for duplicates first
    duplicate_count = check_duplicates(df)
    
    total_score = 0
    unique_calls_per_band = {}
    
    print("\n=== Points Breakdown ===")
    
    for _, row in df.iterrows():
        # Distance points with band multiplier and minimum of 1 km
        distance = calculate_distance(row['sourcegrid'], row['grid'])
        distance_km = max(1, math.ceil(distance))  # Round up and minimum 1 km per QSO
        multiplier = band_multiplier(row['band'])
        distance_points = distance_km * multiplier
        total_score += distance_points

        print(f"{row['call'].upper()}: {distance:.1f} km → {distance_km} km × {multiplier} ({row['band']}) = {distance_points} points")
        
        # Track unique calls per band
        band = normalize_band(row['band'])
        call = row['call'].upper()
        if band not in unique_calls_per_band:
            unique_calls_per_band[band] = set()
        unique_calls_per_band[band].add(call)
    
    # Add 100 points per unique call per band
    bonus_points = 0
    for band, calls in unique_calls_per_band.items():
        band_bonus = len(calls) * 100
        bonus_points += band_bonus
        print(f"\nBand {band}: {len(calls)} unique calls × 100 = {band_bonus} bonus points")
    
    print(f"\nDistance points: {total_score}")
    print(f"Bonus points: {bonus_points}")
    print(f"Total score: {total_score + bonus_points}")
    
    if duplicate_count > 0:
        print(f"\nNOTE: {duplicate_count} duplicate contacts detected above - review log for accuracy")
    
    return total_score + bonus_points

def generate_cabrillo(df, header_info):
    """Generate Cabrillo format output matching k2ua.log format"""
    lines = []
    
    # Header - only essential lines, no X- or HQ- lines
    lines.append("START-OF-LOG: 3.0")
    lines.append(f"CONTEST: {header_info['contest']}")
    lines.append(f"CALLSIGN: {header_info['callsign'].upper()}")
    lines.append(f"CATEGORY-BAND: {header_info['category_band'].upper()}")
    lines.append(f"CATEGORY-OPERATOR: {header_info['category_operator'].upper()}")
    lines.append(f"CATEGORY-MODE: {header_info['category_mode'].upper()}")

    score = header_info['claimed_score'] if header_info['claimed_score'] else calculate_score(df)
    lines.append(f"CLAIMED-SCORE: {score}")
    lines.append(f"CREATED-BY: K2UA Python Logger v{VERSION}")
    
    # QSO lines - format: QSO: <band> <mode> <date> <time> <mycall> <mygrid> <call> <grid>
    for _, row in df.iterrows():
        # Convert time to HHMM format
        time_str = str(row['time']).zfill(4) if len(str(row['time'])) <= 4 else str(row['time'])[:4]
        
        # Convert band to Cabrillo format
        band_formatted = band_to_cabrillo(row['band'])
        
        # Format date as yyyy-mm-dd
        date_str = str(row['date'])
        if '/' in date_str:
            # Convert mm/dd/yyyy to yyyy-mm-dd
            try:
                date_obj = datetime.strptime(date_str, '%m/%d/%Y')
                date_str = date_obj.strftime('%Y-%m-%d')
            except:
                pass
        
        # Format matching k2ua.log: QSO: 10G CW 2022-08-20 1035 K2UA EN91KT KB8VAO EN91IS
        qso_line = f"QSO: {band_formatted} CW {date_str} {time_str} {header_info['callsign'].upper()} {str(row['sourcegrid']).upper()} {str(row['call']).upper()} {str(row['grid']).upper()}"
        lines.append(qso_line)
    
    lines.append("END-OF-LOG:")
    return '\n'.join(lines)

def get_user_input():
    """Get required Cabrillo header information from user.

    This contest has only one operator category (single operator) and one
    mode category (mixed) -- there's no high/low power distinction either --
    so those aren't real choices and aren't prompted for; they're set here
    as fixed values instead. The only things that actually vary are the
    callsign and the band category (10 GHz only, or all bands)."""
    print("\n=== ARRL 10 GHz and Up Contest - Cabrillo Header Information ===\n")

    callsign = input("Your callsign (e.g., W1ABC): ").strip().upper()
    contest = "ARRL-10-GHZ"

    print("\nBand Category:")
    print("  10G = 10 GHz only")
    print("  ALL = All bands")
    category_band = input("Enter band category (10G or ALL): ").strip().upper()

    # Fixed for this contest -- not prompted for.
    category_operator = 'SINGLE-OP'
    category_mode = 'MIXED'
    claimed_score = ''

    return {
        'callsign': callsign,
        'contest': contest,
        'category_operator': category_operator,
        'category_band': category_band,
        'category_mode': category_mode,
        'claimed_score': claimed_score
    }

def main():
    parser = build_arg_parser(
        "Convert a raw QSO log (Google Sheets export or logs/*.csv) into a "
        "Cabrillo-format contest log for the ARRL 10 GHz and Up Contest."
    )
    args = parser.parse_args()

    # Get data (already forward-filled, cleaned, and band-normalized by
    # resolve_source)
    contact_data, _ = load_source_or_exit(args.source)

    # Get user input
    header_info = get_user_input()
    
    # Calculate score
    total_score = calculate_score(contact_data)
    
    # Generate Cabrillo
    cabrillo_content = generate_cabrillo(contact_data, header_info)
    
    # Generate summary
    summary_content = generate_summary(contact_data, header_info, total_score)
    
    # Save files
    cabrillo_filename = f"{header_info['callsign']}_ARRL_10GHZ.log"
    summary_filename = f"{header_info['callsign']}_ARRL_10GHZ_Summary.txt"
    
    with open(cabrillo_filename, 'w') as f:
        f.write(cabrillo_content)
    
    with open(summary_filename, 'w') as f:
        f.write(summary_content)
    
    print(f"\nCabrillo file saved as: {cabrillo_filename}")
    print(f"Summary file saved as: {summary_filename}")
    print(f"Total QSOs: {len(contact_data)}")

if __name__ == "__main__":
    main()
