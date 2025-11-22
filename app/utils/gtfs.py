import os
import requests
import zipfile
import io
import csv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MTA_GTFS_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"

def fetch_gtfs_stations():
    """
    Fetch and parse station data from the official MTA GTFS feed.
    Returns a list of station dictionaries:
    [
        {
            "name": "Station Name",
            "latitude": 40.75,
            "longitude": -73.98,
            "lines": "1,2,3",
            "accessible": 0  # Default, will be updated by other feeds if possible
        },
        ...
    ]
    """
    try:
        logger.info(f"Downloading GTFS data from {MTA_GTFS_URL}...")
        response = requests.get(MTA_GTFS_URL, timeout=30)
        response.raise_for_status()
        
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            # Parse stops.txt
            with z.open('stops.txt') as f:
                stops_content = io.TextIOWrapper(f, encoding='utf-8')
                reader = csv.DictReader(stops_content)
                
                # Store stations by ID
                stations = {}
                
                for row in reader:
                    # We only want parent stations (location_type=1) or stops that represent stations
                    # MTA GTFS structure:
                    # Parent stations usually have IDs like '101', 'R01'
                    # Child stops have IDs like '101N', '101S'
                    
                    stop_id = row['stop_id']
                    
                    # Filter for parent stations (location_type=1)
                    if row.get('location_type') == '1':
                        stations[stop_id] = {
                            "name": row['stop_name'],
                            "latitude": float(row['stop_lat']),
                            "longitude": float(row['stop_lon']),
                            "lines": set(),  # Will populate later
                            "accessible": 0
                        }
            
            logger.info(f"Found {len(stations)} parent stations. Parsing routes...")
            
            # Parse stop_times.txt to link stations to routes (lines)
            # This is a heavy operation, so we'll do a simplified pass
            # We just need to know which route_ids visit which stop_ids
            
            # First, map trip_id -> route_id from trips.txt
            trip_routes = {}
            with z.open('trips.txt') as f:
                trips_content = io.TextIOWrapper(f, encoding='utf-8')
                reader = csv.DictReader(trips_content)
                for row in reader:
                    trip_routes[row['trip_id']] = row['route_id']
            
            # Now scan stop_times.txt to associate routes with stations
            with z.open('stop_times.txt') as f:
                stop_times_content = io.TextIOWrapper(f, encoding='utf-8')
                reader = csv.DictReader(stop_times_content)
                
                for row in reader:
                    trip_id = row['trip_id']
                    stop_id = row['stop_id']
                    
                    # Get parent station ID (usually first 3 chars for MTA, e.g. 101N -> 101)
                    # But some are different. Let's try to find the parent in our stations dict.
                    # MTA convention: parent is stop_id without N/S suffix
                    parent_id = stop_id
                    if stop_id not in stations:
                        if stop_id.endswith('N') or stop_id.endswith('S'):
                            parent_id = stop_id[:-1]
                    
                    if parent_id in stations and trip_id in trip_routes:
                        route_id = trip_routes[trip_id]
                        stations[parent_id]['lines'].add(route_id)
            
            # Convert sets to sorted strings and format for return
            result = []
            for s in stations.values():
                if s['lines']:  # Only include stations that have service
                    s['lines'] = ",".join(sorted(s['lines']))
                    result.append(s)
            
            logger.info(f"Successfully parsed {len(result)} active stations.")
            return result

    except Exception as e:
        logger.error(f"Error fetching GTFS data: {e}")
        return []
