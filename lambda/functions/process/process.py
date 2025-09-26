import json
import boto3
import os
import uuid
import tempfile
import zipfile
from datetime import datetime, timedelta
import pandas as pd
import requests
from io import StringIO, BytesIO
import math
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict

s3_client = boto3.client('s3')
FILES_BUCKET = os.environ['FILES_BUCKET']

def handler(event, context):
    try:
        # Parse the request
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        contest_data = json.loads(body['contestData'])
        
        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            # Process based on input type
            if contest_data['inputType'] == 'files':
                # Handle file uploads (would need multipart parsing in real implementation)
                # For now, simulate with sample data
                df = create_sample_data()
            else:
                # Handle Google Sheets URL
                df = get_sheet_data(contest_data['sheetsUrl'])
            
            # Generate requested outputs
            output_files = []
            callsign = contest_data['callsign']
            year = contest_data['contestYear']
            
            # Get the latest contest date from the data
            if not df.empty and 'date' in df.columns:
                latest_date = df['date'].max()
                date_str = latest_date if isinstance(latest_date, str) else latest_date.strftime('%Y-%m-%d')
            else:
                date_str = f"{year}-01-01"
            
            for output_type in contest_data['outputs']:
                if output_type == 'cabrillo':
                    filename = f"{callsign}_ARRL_10GHZ_{date_str}.log"
                    filepath = os.path.join(temp_dir, filename)
                    generate_cabrillo(df, contest_data, filepath)
                    output_files.append((filepath, filename))
                
                elif output_type == 'summary':
                    filename = f"{callsign}_Summary_{date_str}.txt"
                    filepath = os.path.join(temp_dir, filename)
                    generate_summary(df, contest_data, filepath)
                    output_files.append((filepath, filename))
                
                elif output_type == 'station_report':
                    filename = f"{callsign}_Station_Report_{date_str}.txt"
                    filepath = os.path.join(temp_dir, filename)
                    generate_station_report(df, contest_data, filepath)
                    output_files.append((filepath, filename))
                
                elif output_type == 'weekend_analysis':
                    filename = f"{callsign}_Weekend_Analysis_{date_str}.txt"
                    filepath = os.path.join(temp_dir, filename)
                    generate_weekend_analysis(df, contest_data, filepath)
                    output_files.append((filepath, filename))
                
                elif output_type == 'comprehensive_analysis':
                    filename = f"{callsign}_Comprehensive_Analysis_{date_str}.txt"
                    filepath = os.path.join(temp_dir, filename)
                    generate_comprehensive_analysis(df, contest_data, filepath)
                    output_files.append((filepath, filename))
                
                elif output_type == 'directional_viz':
                    filename = f"{callsign}_Directional_Viz_{date_str}.png"
                    filepath = os.path.join(temp_dir, filename)
                    generate_directional_visualization(df, contest_data, filepath)
                    output_files.append((filepath, filename))
            
            # Upload files to S3 and generate download URLs
            download_urls = []
            for filepath, filename in output_files:
                s3_key = f"results/{uuid.uuid4()}/{filename}"
                
                with open(filepath, 'rb') as f:
                    s3_client.put_object(
                        Bucket=FILES_BUCKET,
                        Key=s3_key,
                        Body=f.read(),
                        ContentDisposition=f'attachment; filename="{filename}"'
                    )
                
                # Generate presigned URL (valid for 1 hour)
                url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': FILES_BUCKET, 'Key': s3_key},
                    ExpiresIn=3600
                )
                
                download_urls.append({
                    'name': filename,
                    'url': url
                })
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'success': True,
                'files': download_urls
            })
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'success': False,
                'message': str(e)
            })
        }

def create_sample_data():
    """Create sample contest data for demonstration"""
    data = {
        'date': ['2024-08-17', '2024-08-17', '2024-08-18'],
        'time': ['1400', '1430', '0900'],
        'call': ['W1ABC', 'K2DEF', 'N3GHI'],
        'band': ['10GHz', '24GHz', '10GHz'],
        'grid': ['FN20aa', 'FN30bb', 'FN21cc'],
        'sourcegrid': ['FN20xx', 'FN20xx', 'FN20xx']
    }
    return pd.DataFrame(data)

def get_sheet_data(sheet_url):
    """Convert Google Sheets URL to CSV export URL and fetch data"""
    try:
        sheet_id = sheet_url.split('/d/')[1].split('/')[0]
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        response = requests.get(csv_url, timeout=30)
        response.raise_for_status()
        return pd.read_csv(StringIO(response.text))
    except Exception as e:
        raise Exception(f"Failed to fetch Google Sheets data: {str(e)}")

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

def normalize_band(band):
    """Normalize band name to standard format"""
    band_str = str(band).strip().lower()
    if 'ghz' in band_str:
        import re
        match = re.search(r'(\d+(?:\.\d+)?)', band_str)
        if match:
            freq = float(match.group(1))
            return f"{int(freq)}GHz" if freq == int(freq) else f"{freq}GHz"
    return str(band).strip()

def get_band_multiplier(band):
    """Get scoring multiplier for band"""
    multipliers = {
        '10GHz': 1, '24GHz': 2, '47GHz': 3, '78GHz': 4, '122GHz': 5,
        '134GHz': 6, '241GHz': 7, '300GHz': 8
    }
    return multipliers.get(normalize_band(band), 1)

def generate_cabrillo(df, contest_data, filepath):
    """Generate Cabrillo format log file"""
    with open(filepath, 'w') as f:
        f.write("START-OF-LOG: 3.0\n")
        f.write(f"CALLSIGN: {contest_data['callsign']}\n")
        f.write("CONTEST: ARRL-10-GHZ\n")
        f.write(f"CATEGORY-OPERATOR: {contest_data['operatorCategory']}\n")
        f.write(f"CATEGORY-STATION: {contest_data['stationCategory']}\n")
        f.write(f"GRID-LOCATOR: {contest_data['gridSquare']}\n")
        
        if contest_data.get('power'):
            f.write(f"CATEGORY-POWER: {contest_data['power']}W\n")
        
        f.write(f"CLAIMED-SCORE: 0\n")  # Will be calculated
        
        # QSO lines
        for _, row in df.iterrows():
            band = normalize_band(row['band'])
            date = row['date'].replace('-', '') if isinstance(row['date'], str) else row['date'].strftime('%Y%m%d')
            time = str(row['time']).zfill(4)
            
            f.write(f"QSO: {band} PH {date} {time} {contest_data['callsign']} "
                   f"{contest_data['gridSquare']} {row['call']} {row['grid']}\n")
        
        f.write("END-OF-LOG:\n")

def generate_summary(df, contest_data, filepath):
    """Generate contest summary report"""
    with open(filepath, 'w') as f:
        f.write(f"ARRL 10 GHz Contest Summary - {contest_data['callsign']}\n")
        f.write("=" * 50 + "\n\n")
        
        total_qsos = len(df)
        unique_calls = df['call'].nunique() if not df.empty else 0
        bands_worked = df['band'].nunique() if not df.empty else 0
        
        f.write(f"Total QSOs: {total_qsos}\n")
        f.write(f"Unique Callsigns: {unique_calls}\n")
        f.write(f"Bands Worked: {bands_worked}\n\n")
        
        if not df.empty:
            f.write("QSOs by Band:\n")
            band_counts = df['band'].value_counts()
            for band, count in band_counts.items():
                f.write(f"  {normalize_band(band)}: {count}\n")

def generate_station_report(df, contest_data, filepath):
    """Generate station-by-station activity report"""
    with open(filepath, 'w') as f:
        f.write(f"Station Activity Report - {contest_data['callsign']}\n")
        f.write("=" * 50 + "\n\n")
        
        if df.empty:
            f.write("No QSO data available.\n")
            return
        
        station_stats = df.groupby('call').agg({
            'band': 'count',
            'grid': 'first'
        }).rename(columns={'band': 'qsos'})
        
        f.write("Station\tQSOs\tGrid\n")
        f.write("-" * 30 + "\n")
        
        for call, stats in station_stats.iterrows():
            f.write(f"{call}\t{stats['qsos']}\t{stats['grid']}\n")

def generate_weekend_analysis(df, contest_data, filepath):
    """Generate weekend-by-weekend analysis"""
    with open(filepath, 'w') as f:
        f.write(f"Weekend Analysis - {contest_data['callsign']}\n")
        f.write("=" * 50 + "\n\n")
        
        if df.empty:
            f.write("No QSO data available.\n")
            return
        
        # Group by date
        daily_stats = df.groupby('date').agg({
            'call': 'count',
            'band': lambda x: x.nunique()
        }).rename(columns={'call': 'qsos', 'band': 'bands'})
        
        f.write("Date\t\tQSOs\tBands\n")
        f.write("-" * 30 + "\n")
        
        for date, stats in daily_stats.iterrows():
            f.write(f"{date}\t{stats['qsos']}\t{stats['bands']}\n")

def generate_comprehensive_analysis(df, contest_data, filepath):
    """Generate comprehensive contest analysis"""
    with open(filepath, 'w') as f:
        f.write(f"Comprehensive Analysis - {contest_data['callsign']}\n")
        f.write("=" * 60 + "\n\n")
        
        if df.empty:
            f.write("No QSO data available.\n")
            return
        
        # Basic statistics
        f.write("CONTEST OVERVIEW\n")
        f.write("-" * 20 + "\n")
        f.write(f"Total QSOs: {len(df)}\n")
        f.write(f"Unique Stations: {df['call'].nunique()}\n")
        f.write(f"Bands Active: {df['band'].nunique()}\n\n")
        
        # Distance analysis
        if 'sourcegrid' in df.columns and 'grid' in df.columns:
            distances = []
            for _, row in df.iterrows():
                dist = calculate_distance(row['sourcegrid'], row['grid'])
                distances.append(dist)
            
            if distances:
                f.write("DISTANCE ANALYSIS\n")
                f.write("-" * 20 + "\n")
                f.write(f"Average Distance: {np.mean(distances):.1f} km\n")
                f.write(f"Maximum Distance: {max(distances):.1f} km\n")
                f.write(f"Minimum Distance: {min(distances):.1f} km\n\n")

def generate_directional_visualization(df, contest_data, filepath):
    """Generate directional visualization plot"""
    if df.empty:
        # Create empty plot
        fig, ax = plt.subplots(figsize=(10, 8))
        ax.text(0.5, 0.5, 'No QSO data available', ha='center', va='center', transform=ax.transAxes)
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close()
        return
    
    # Create polar plot for directional analysis
    fig, ax = plt.subplots(figsize=(10, 8), subplot_kw=dict(projection='polar'))
    
    # Calculate bearings if we have grid data
    if 'sourcegrid' in df.columns and 'grid' in df.columns:
        bearings = []
        distances = []
        
        for _, row in df.iterrows():
            bearing = calculate_bearing(row['sourcegrid'], row['grid'])
            distance = calculate_distance(row['sourcegrid'], row['grid'])
            if bearing is not None:
                bearings.append(math.radians(bearing))
                distances.append(distance)
        
        if bearings:
            # Plot QSOs as points
            ax.scatter(bearings, distances, alpha=0.6, s=50)
            ax.set_title(f"Directional Analysis - {contest_data['callsign']}", pad=20)
            ax.set_theta_zero_location('N')
            ax.set_theta_direction(-1)
            ax.set_ylabel('Distance (km)')
        else:
            ax.text(0, 0, 'Insufficient grid data for directional analysis', ha='center', va='center')
    else:
        ax.text(0, 0, 'No grid square data available', ha='center', va='center')
    
    plt.savefig(filepath, dpi=150, bbox_inches='tight')
    plt.close()

def calculate_bearing(grid1, grid2):
    """Calculate bearing from grid1 to grid2"""
    lat1, lon1 = grid_to_latlon(grid1)
    lat2, lon2 = grid_to_latlon(grid2)
    
    if lat1 == 0 and lon1 == 0:
        return None
    
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    
    bearing = math.atan2(y, x)
    bearing = math.degrees(bearing)
    bearing = (bearing + 360) % 360
    
    return bearing