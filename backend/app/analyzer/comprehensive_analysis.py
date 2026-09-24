#!/usr/bin/env python3

import math
from datetime import datetime, timedelta
from collections import defaultdict

from data_source import (
    load_source_or_exit, build_arg_parser,
    normalize_band, band_multiplier, BAND_ORDER,
)

def grid_to_latlon(grid):
    """Convert 6-digit Maidenhead grid to lat/lon"""
    grid = str(grid).upper().strip()
    if len(grid) < 4:
        return 0.0, 0.0
    if len(grid) < 6:
        grid = grid + 'AA'[:6-len(grid)]
    grid = grid[:6]
    
    try:
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90
        lon += (ord(grid[2]) - ord('0')) * 2
        lat += (ord(grid[3]) - ord('0')) * 1
        lon += (ord(grid[4]) - ord('A')) * 5/60
        lat += (ord(grid[5]) - ord('A')) * 2.5/60
        return lat + 1.25/60, lon + 2.5/60
    except (ValueError, IndexError):
        return 0.0, 0.0

def calculate_distance(grid1, grid2):
    """Calculate distance between two grids in km"""
    lat1, lon1 = grid_to_latlon(grid1)
    lat2, lon2 = grid_to_latlon(grid2)
    
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return 6371 * 2 * math.asin(math.sqrt(a))

def calculate_bearing(grid1, grid2):
    """Calculate bearing from grid1 to grid2"""
    lat1, lon1 = grid_to_latlon(grid1)
    lat2, lon2 = grid_to_latlon(grid2)
    
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.atan2(y, x)
    bearing = math.degrees(bearing)
    bearing = (bearing + 360) % 360
    return bearing

def get_direction(bearing):
    """Convert bearing to compass direction"""
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
                 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = round(bearing / 22.5) % 16
    return directions[idx]

def parse_datetime(date_str, time_str):
    """Parse date and time strings into datetime object"""
    try:
        if '/' in date_str:
            date_parts = date_str.split('/')
            if len(date_parts[2]) == 4:
                date_obj = datetime.strptime(date_str, '%m/%d/%Y')
            else:
                date_obj = datetime.strptime(date_str, '%m/%d/%y')
        else:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        
        time_str = str(time_str).zfill(4)
        hour = int(time_str[:2])
        minute = int(time_str[2:])
        
        return date_obj.replace(hour=hour, minute=minute)
    except:
        return None

def generate_comprehensive_analysis(df):
    """Generate comprehensive contest analysis"""
    lines = []
    
    # Header
    lines.append("ARRL 10 GHz and Up Contest - Comprehensive Analysis Report")
    lines.append("=" * 70)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append("")
    
    # Add analysis columns
    df['distance'] = df.apply(lambda row: calculate_distance(row['sourcegrid'], row['grid']), axis=1)
    df['bearing'] = df.apply(lambda row: calculate_bearing(row['sourcegrid'], row['grid']), axis=1)
    df['direction'] = df['bearing'].apply(get_direction)
    df['band_normalized'] = df['band'].apply(normalize_band)
    df['band_multiplier'] = df['band'].apply(band_multiplier)
    df['points'] = df.apply(lambda row: max(1, math.ceil(row['distance'])) * row['band_multiplier'], axis=1)
    df['datetime'] = df.apply(lambda row: parse_datetime(row['date'], row['time']), axis=1)
    
    # CONTEST SUMMARY
    lines.append("CONTEST SUMMARY")
    lines.append("=" * 20)
    total_qsos = len(df)
    unique_stations = df['call'].nunique()
    total_distance_points = df['points'].sum()
    
    # Calculate bonus points
    band_unique_calls = {}
    for _, row in df.iterrows():
        band = normalize_band(row['band'])
        call = row['call'].upper()
        if band not in band_unique_calls:
            band_unique_calls[band] = set()
        band_unique_calls[band].add(call)
    
    total_bonus = sum(len(calls) * 100 for calls in band_unique_calls.values())
    total_score = total_distance_points + total_bonus
    
    lines.append(f"Total QSOs: {total_qsos}")
    lines.append(f"Unique Stations: {unique_stations}")
    lines.append(f"Distance Points: {total_distance_points:,}")
    lines.append(f"Bonus Points: {total_bonus:,}")
    lines.append(f"Total Score: {total_score:,}")
    lines.append(f"Average Distance: {df['distance'].mean():.1f} km")
    lines.append(f"Best DX: {df['distance'].max():.1f} km")
    lines.append(f"Bands Active: {df['band_normalized'].nunique()}")
    lines.append("")
    
    # DETAILED QSO LOG WITH ANALYSIS
    lines.append("DETAILED QSO LOG WITH DISTANCE AND BEARING ANALYSIS")
    lines.append("=" * 60)
    lines.append("Date     Time Call     Band    Grid     Dist(km) Bear Dir Points")
    lines.append("-" * 65)
    
    for _, row in df.iterrows():
        date_str = str(row['date'])[:10] if len(str(row['date'])) > 10 else str(row['date'])
        time_str = str(row['time']).zfill(4)
        call = str(row['call']).upper()[:8]
        band = str(row['band'])[:7]
        grid = str(row['grid']).upper()[:8]
        distance = row['distance']
        bearing = row['bearing']
        direction = row['direction']
        points = row['points']
        
        lines.append(f"{date_str} {time_str} {call:<8} {band:<7} {grid:<8} {distance:6.1f}  {bearing:5.1f} {direction:<3} {points:6d}")
    
    lines.append("")
    lines.append("")
    
    # STATION ANALYSIS
    lines.append("STATION ANALYSIS - BREAKDOWN BY CALLSIGN")
    lines.append("=" * 45)
    lines.append("")
    
    station_data = {}
    for _, row in df.iterrows():
        call = row['call'].upper()
        band = normalize_band(row['band'])
        distance = row['distance']
        points = row['points']
        
        if call not in station_data:
            station_data[call] = {
                'total_qsos': 0,
                'total_points': 0,
                'bands': {},
                'best_dx': 0,
                'avg_distance': 0,
                'distances': []
            }
        
        station_data[call]['total_qsos'] += 1
        station_data[call]['total_points'] += points
        station_data[call]['best_dx'] = max(station_data[call]['best_dx'], distance)
        station_data[call]['distances'].append(distance)
        
        if band not in station_data[call]['bands']:
            station_data[call]['bands'][band] = {
                'count': 0,
                'best_dx': 0,
                'total_points': 0
            }
        
        station_data[call]['bands'][band]['count'] += 1
        station_data[call]['bands'][band]['best_dx'] = max(station_data[call]['bands'][band]['best_dx'], distance)
        station_data[call]['bands'][band]['total_points'] += points
    
    # Calculate averages
    for call in station_data:
        station_data[call]['avg_distance'] = sum(station_data[call]['distances']) / len(station_data[call]['distances'])
    
    # Sort by total QSOs
    sorted_stations = sorted(station_data.items(), key=lambda x: x[1]['total_qsos'], reverse=True)
    
    for call, data in sorted_stations:
        lines.append(f"Station: {call}")
        lines.append(f"  Total QSOs: {data['total_qsos']}")
        lines.append(f"  Total Points: {data['total_points']:,}")
        lines.append(f"  Best DX: {data['best_dx']:.1f} km")
        lines.append(f"  Average Distance: {data['avg_distance']:.1f} km")
        lines.append("  Band Breakdown:")
        
        # data['bands'] is keyed by canonical display name (via
        # normalize_band), so this is a direct lookup against BAND_ORDER.
        for band_name in BAND_ORDER:
            if band_name in data['bands']:
                band_info = data['bands'][band_name]
                lines.append(f"    {band_name}: {band_info['count']} QSOs, Best DX: {band_info['best_dx']:.1f} km, Points: {band_info['total_points']}")
        
        lines.append("")
    
    # Band analysis
    lines.append("BAND ANALYSIS")
    lines.append("=" * 15)
    lines.append("")
    
    band_stats = df.groupby('band_normalized').agg({
        'call': ['count', 'nunique'],
        'distance': ['mean', 'max', 'min'],
        'points': 'sum'
    }).round(1)
    
    lines.append("Band        QSOs  Unique  Avg Dist  Best DX   Min Dist  Points")
    lines.append("-" * 60)
    
    for band in band_stats.index:
        qsos = int(band_stats.loc[band, ('call', 'count')])
        unique = int(band_stats.loc[band, ('call', 'nunique')])
        avg_dist = band_stats.loc[band, ('distance', 'mean')]
        best_dx = band_stats.loc[band, ('distance', 'max')]
        min_dist = band_stats.loc[band, ('distance', 'min')]
        points = int(band_stats.loc[band, ('points', 'sum')])
        
        lines.append(f"{band:<11} {qsos:4d}  {unique:6d}  {avg_dist:8.1f}  {best_dx:8.1f}  {min_dist:8.1f}  {points:6d}")
    
    lines.append("")
    
    # DIRECTIONAL ANALYSIS
    lines.append("DIRECTIONAL ANALYSIS")
    lines.append("=" * 20)
    lines.append("")
    
    direction_stats = df.groupby('direction').agg({
        'call': 'count',
        'distance': ['mean', 'max'],
        'points': 'sum'
    }).round(1)
    
    lines.append("Direction  QSOs  Avg Dist  Best DX   Points")
    lines.append("-" * 40)
    
    for direction in ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 
                     'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']:
        if direction in direction_stats.index:
            stats = direction_stats.loc[direction]
            qsos = int(stats[('call', 'count')])
            avg_dist = stats[('distance', 'mean')]
            best_dx = stats[('distance', 'max')]
            points = int(stats[('points', 'sum')])
            
            lines.append(f"{direction:<9}  {qsos:4d}  {avg_dist:8.1f}  {best_dx:8.1f}  {points:6d}")
    
    lines.append("")
    
    # OPERATING LOCATIONS
    lines.append("OPERATING LOCATIONS ANALYSIS")
    lines.append("=" * 30)
    lines.append("")
    
    location_stats = df.groupby('sourcegrid').agg({
        'call': 'count',
        'distance': ['mean', 'max'],
        'points': 'sum'
    }).round(1)
    
    lines.append("Grid      QSOs  Avg Dist  Best DX   Points")
    lines.append("-" * 38)
    
    for grid in location_stats.index:
        stats = location_stats.loc[grid]
        qsos = int(stats[('call', 'count')])
        avg_dist = stats[('distance', 'mean')]
        best_dx = stats[('distance', 'max')]
        points = int(stats[('points', 'sum')])
        
        lines.append(f"{str(grid).upper():<8}  {qsos:4d}  {avg_dist:8.1f}  {best_dx:8.1f}  {points:6d}")
    
    lines.append("")
    
    # TIME ANALYSIS
    lines.append("TIME ANALYSIS")
    lines.append("=" * 15)
    lines.append("")
    
    df['hour'] = df['datetime'].apply(lambda x: x.hour if x else None)
    hourly_stats = df.groupby('hour').agg({
        'call': 'count',
        'distance': 'mean',
        'points': 'sum'
    }).round(1)
    
    lines.append("Hour  QSOs  Avg Dist  Points")
    lines.append("-" * 25)
    
    for hour in sorted(hourly_stats.index):
        if hour is not None:
            stats = hourly_stats.loc[hour]
            qsos = int(stats['call'])
            avg_dist = stats['distance']
            points = int(stats['points'])
            
            lines.append(f"{hour:02d}00  {qsos:4d}  {avg_dist:8.1f}  {points:6d}")
    
    return '\n'.join(lines)

def main():
    parser = build_arg_parser(
        "Generate a comprehensive contest analysis report from a Cabrillo "
        "log, a raw QSO CSV, or a Google Sheets share URL."
    )
    args = parser.parse_args()

    contact_data, callsign = load_source_or_exit(args.source)

    # Generate comprehensive analysis
    analysis = generate_comprehensive_analysis(contact_data)
    
    # Generate unique filename
    filename = get_output_filename(contact_data, "Comprehensive_Analysis", callsign)
    
    # Save report
    with open(filename, 'w') as f:
        f.write(analysis)
    
    print(f"Comprehensive analysis saved as: {filename}")
    print(f"Total QSOs analyzed: {len(contact_data)}")
    print("Report includes:")
    print("- Complete QSO log with distances and bearings")
    print("- Detailed station-by-station breakdown")
    print("- Band, directional, and time analysis")
    print("- Operating location statistics")

def get_output_filename(contact_data, base_name, callsign="UNKNOWN"):
    """Generate unique filename based on callsign and last contest date"""
    dates = contact_data['date'].unique()
    last_date = max(dates)
    
    # Handle different date formats
    if '/' in str(last_date):
        # Convert mm/dd/yyyy to yyyymmdd
        try:
            from datetime import datetime
            date_obj = datetime.strptime(str(last_date), '%m/%d/%Y')
            last_date = date_obj.strftime('%Y%m%d')
        except:
            last_date = str(last_date).replace('/', '')
    else:
        # Handle yyyy-mm-dd format
        last_date = str(last_date).replace('-', '')
    
    return f"{callsign}_{base_name}_{last_date}.txt"

if __name__ == "__main__":
    main()
