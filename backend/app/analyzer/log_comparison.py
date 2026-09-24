#!/usr/bin/env python3

from datetime import datetime, timedelta
import math
import sys

from data_source import resolve_source, source_label, build_multi_source_arg_parser, normalize_band

def grid_to_latlon(grid):
    """Convert 6-digit Maidenhead grid to lat/lon"""
    grid = str(grid).upper().strip()
    if len(grid) < 4:
        return None, None
    
    lon = (ord(grid[0]) - ord('A')) * 20 - 180
    lat = (ord(grid[1]) - ord('A')) * 10 - 90
    lon += (ord(grid[2]) - ord('0')) * 2
    lat += (ord(grid[3]) - ord('0')) * 1
    
    if len(grid) >= 6:
        lon += (ord(grid[4]) - ord('A')) * (2/24)
        lat += (ord(grid[5]) - ord('A')) * (1/24)
    
    return lat, lon

def calculate_distance(grid1, grid2):
    """Calculate distance between two grids in km"""
    lat1, lon1 = grid_to_latlon(grid1)
    lat2, lon2 = grid_to_latlon(grid2)
    
    if None in [lat1, lon1, lat2, lon2]:
        return 0
    
    R = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def analyze_log(df, callsign, filename):
    """Analyze a single log and return key metrics"""
    if df.empty:
        return {}
    
    # Add distance calculations
    df['distance'] = df.apply(lambda row: calculate_distance(row['sourcegrid'], row['grid']), axis=1)
    df['band_normalized'] = df['band'].apply(normalize_band)
    
    # Basic stats
    total_qsos = len(df)
    unique_stations = df['call'].nunique()
    unique_grids = df['sourcegrid'].nunique()
    
    # Date range
    dates = sorted(df['date'].unique())
    date_range = f"{dates[0]} to {dates[-1]}" if len(dates) > 1 else dates[0]
    
    # Weekend breakdown
    weekends = {}
    for date in dates:
        weekend_key = date[:7]  # YYYY-MM format
        if weekend_key not in weekends:
            weekends[weekend_key] = 0
        weekends[weekend_key] += len(df[df['date'] == date])
    
    # Band analysis
    band_stats = {}
    for band in df['band_normalized'].unique():
        band_data = df[df['band_normalized'] == band]
        band_stats[band] = {
            'qsos': len(band_data),
            'unique_stations': band_data['call'].nunique(),
            'best_dx': band_data['distance'].max(),
            'avg_distance': band_data['distance'].mean()
        }
    
    # Distance metrics
    total_distance = df['distance'].sum()
    best_dx = df['distance'].max()
    avg_distance = df['distance'].mean()
    
    # Grid activation distance (sum of unique distances between grids)
    unique_grids_list = df['sourcegrid'].unique()
    grid_distances = []
    for i in range(len(unique_grids_list)):
        for j in range(i+1, len(unique_grids_list)):
            dist = calculate_distance(unique_grids_list[i], unique_grids_list[j])
            if dist > 0:
                grid_distances.append(dist)
    
    total_grid_travel = sum(grid_distances) if grid_distances else 0
    
    return {
        'callsign': callsign,
        'filename': filename,
        'total_qsos': total_qsos,
        'unique_stations': unique_stations,
        'unique_grids': unique_grids,
        'date_range': date_range,
        'weekends': weekends,
        'band_stats': band_stats,
        'total_distance': total_distance,
        'best_dx': best_dx,
        'avg_distance': avg_distance,
        'total_grid_travel': total_grid_travel,
        'bands_active': len(band_stats)
    }

def generate_comparison_report(analyses):
    """Generate comprehensive comparison report"""
    lines = []
    lines.append("ARRL 10 GHz Contest - Log Comparison Analysis")
    lines.append("=" * 60)
    lines.append("")
    
    # Summary table
    lines.append("SUMMARY COMPARISON")
    lines.append("-" * 40)
    lines.append(f"{'Station':<12} {'QSOs':<6} {'Stations':<8} {'Grids':<6} {'Bands':<6} {'Best DX':<8}")
    lines.append("-" * 40)
    
    for analysis in analyses:
        lines.append(f"{analysis['callsign']:<12} {analysis['total_qsos']:<6} "
                    f"{analysis['unique_stations']:<8} {analysis['unique_grids']:<6} "
                    f"{analysis['bands_active']:<6} {analysis['best_dx']:<8.0f}")
    
    lines.append("")
    
    # Band comparison
    all_bands = set()
    for analysis in analyses:
        all_bands.update(analysis['band_stats'].keys())
    
    lines.append("BAND COMPARISON")
    lines.append("-" * 60)
    for band in sorted(all_bands):
        lines.append(f"\n{band}:")
        lines.append(f"{'Station':<12} {'QSOs':<6} {'Stations':<8} {'Best DX':<8} {'Avg Dist':<8}")
        lines.append("-" * 44)
        
        for analysis in analyses:
            if band in analysis['band_stats']:
                stats = analysis['band_stats'][band]
                lines.append(f"{analysis['callsign']:<12} {stats['qsos']:<6} "
                           f"{stats['unique_stations']:<8} {stats['best_dx']:<8.0f} "
                           f"{stats['avg_distance']:<8.0f}")
            else:
                lines.append(f"{analysis['callsign']:<12} {'0':<6} {'0':<8} {'0':<8} {'0':<8}")
    
    lines.append("")
    
    # Weekend comparison
    lines.append("WEEKEND ACTIVITY COMPARISON")
    lines.append("-" * 40)
    all_weekends = set()
    for analysis in analyses:
        all_weekends.update(analysis['weekends'].keys())
    
    for weekend in sorted(all_weekends):
        lines.append(f"\n{weekend} Weekend:")
        for analysis in analyses:
            qsos = analysis['weekends'].get(weekend, 0)
            lines.append(f"  {analysis['callsign']}: {qsos} QSOs")
    
    lines.append("")
    
    # Distance and mobility analysis
    lines.append("DISTANCE & MOBILITY ANALYSIS")
    lines.append("-" * 50)
    lines.append(f"{'Station':<12} {'Total Dist':<10} {'Grid Travel':<12} {'Avg QSO':<8}")
    lines.append("-" * 42)
    
    for analysis in analyses:
        lines.append(f"{analysis['callsign']:<12} {analysis['total_distance']:<10.0f} "
                    f"{analysis['total_grid_travel']:<12.0f} {analysis['avg_distance']:<8.0f}")
    
    lines.append("")
    
    # Key insights
    lines.append("KEY INSIGHTS & OBSERVATIONS")
    lines.append("-" * 30)
    
    # Find leaders in each category
    best_qsos = max(analyses, key=lambda x: x['total_qsos'])
    best_dx = max(analyses, key=lambda x: x['best_dx'])
    most_mobile = max(analyses, key=lambda x: x['total_grid_travel'])
    most_bands = max(analyses, key=lambda x: x['bands_active'])
    
    lines.append(f"• Most QSOs: {best_qsos['callsign']} ({best_qsos['total_qsos']} contacts)")
    lines.append(f"• Best DX: {best_dx['callsign']} ({best_dx['best_dx']:.0f} km)")
    lines.append(f"• Most Mobile: {most_mobile['callsign']} ({most_mobile['total_grid_travel']:.0f} km grid travel)")
    lines.append(f"• Most Bands: {most_bands['callsign']} ({most_bands['bands_active']} bands active)")
    
    # Performance ratios
    if len(analyses) >= 2:
        lines.append("")
        lines.append("PERFORMANCE RATIOS:")
        base = analyses[0]
        for i, comp in enumerate(analyses[1:], 1):
            qso_ratio = comp['total_qsos'] / base['total_qsos'] if base['total_qsos'] > 0 else 0
            dx_ratio = comp['best_dx'] / base['best_dx'] if base['best_dx'] > 0 else 0
            lines.append(f"• {comp['callsign']} vs {base['callsign']}: "
                        f"{qso_ratio:.2f}x QSOs, {dx_ratio:.2f}x DX")
    
    return '\n'.join(lines)

def main():
    parser = build_multi_source_arg_parser(
        "Compare 2-4 contest logs side by side. Each source may be a "
        "Cabrillo .log file, a raw QSO CSV, or a Google Sheets share URL."
    )
    args = parser.parse_args()

    if len(args.sources) < 2:
        print("Error: Need at least 2 sources for comparison")
        print("Usage: python log_comparison.py <log1.log> <log2.log> [log3.log] [log4.log]")
        print("Example: python log_comparison.py k2ua_2022.log k2ua_2023.log")
        sys.exit(1)

    sources = args.sources[:4]  # Max 4 sources
    analyses = []

    print("Loading and analyzing log files...")
    for source in sources:
        label = source_label(source)
        try:
            df, callsign = resolve_source(source, verbose=False)
            if callsign == "UNKNOWN":
                # Cabrillo files carry their own callsign; other sources don't --
                # fall back to the source's label so the report stays readable.
                callsign = label
            analysis = analyze_log(df, callsign, label)
            analyses.append(analysis)
            print(f"Loaded {label}: {analysis['total_qsos']} QSOs")
        except Exception as e:
            print(f"Error processing {source}: {e}")

    if len(analyses) < 2:
        print("Error: Need at least 2 valid log files for comparison")
        sys.exit(1)
    
    # Generate report
    report = generate_comparison_report(analyses)
    
    # Generate filename
    callsigns = "_vs_".join([a['callsign'] for a in analyses])
    output_filename = f"Log_Comparison_{callsigns}.txt"
    
    # Save report
    with open(output_filename, 'w') as f:
        f.write(report)
    
    print(f"\nComparison report saved as: {output_filename}")
    print(f"Compared {len(analyses)} log files")

if __name__ == "__main__":
    main()
