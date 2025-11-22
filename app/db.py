import sqlite3
import os
import requests
import time
from flask import g, current_app
from app.utils.gtfs import fetch_gtfs_stations

def get_db():
    """Get database connection from Flask app context."""
    if 'db' not in g:
        db_path = os.path.join(current_app.instance_path, 'alwaysontime.db')
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    """Close database connection."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """Initialize database with schema."""
    db = get_db()
    
    # Stations table
    db.execute('''
        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            lines TEXT,
            accessible INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Alerts cache table
    db.execute('''
        CREATE TABLE IF NOT EXISTS alerts_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT UNIQUE NOT NULL,
            header_text TEXT,
            description_text TEXT,
            affected_lines TEXT,
            severity TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    ''')
    
    # User favorites
    db.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            station_id INTEGER,
            route_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (station_id) REFERENCES stations(id),
            UNIQUE(user_id, station_id, route_id)
        )
    ''')
    
    # Station entrances table
    db.execute('''
        CREATE TABLE IF NOT EXISTS station_entrances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            description TEXT,
            accessible INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )
    ''')
    
    # Migration: Add route_id column if it doesn't exist
    try:
        db.execute('ALTER TABLE favorites ADD COLUMN route_id TEXT')
    except Exception:
        pass  # Column already exists
    
    # Route learning insights table (stores patterns learned from Google Maps)
    db.execute('''
        CREATE TABLE IF NOT EXISTS route_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin_lat REAL NOT NULL,
            origin_lon REAL NOT NULL,
            dest_lat REAL NOT NULL,
            dest_lon REAL NOT NULL,
            route_hash TEXT NOT NULL,
            google_time REAL,
            our_time REAL,
            time_diff REAL,
            google_stations TEXT,
            our_stations TEXT,
            insights TEXT,
            sample_count INTEGER DEFAULT 1,
            confidence REAL DEFAULT 0.0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(route_hash)
        )
    ''')
    
    # Global route patterns table (learns general patterns, not specific routes)
    db.execute('''
        CREATE TABLE IF NOT EXISTS route_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT NOT NULL,
            pattern_key TEXT NOT NULL,
            correction_factor REAL DEFAULT 0.0,
            sample_count INTEGER DEFAULT 1,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(pattern_type, pattern_key)
        )
    ''')
    
    # Create index for faster lookups
    try:
        db.execute('CREATE INDEX IF NOT EXISTS idx_route_hash ON route_insights(route_hash)')
    except Exception:
        pass
    
    db.commit()

def seed_stations():
    """Seed database with stations from official MTA GTFS data."""
    db = get_db()
    
    print("Fetching station data from MTA GTFS feed...")
    stations = fetch_gtfs_stations()
    
    if not stations:
        print("Failed to fetch GTFS data. Aborting station seed.")
        return
        
    print(f"Seeding {len(stations)} stations...")
    
    # Clear existing stations
    db.execute('DELETE FROM stations')
    db.execute('DELETE FROM sqlite_sequence WHERE name="stations"')
    
    count = 0
    for s in stations:
        db.execute(
            'INSERT INTO stations (name, latitude, longitude, lines, accessible) VALUES (?, ?, ?, ?, ?)',
            (s['name'], s['latitude'], s['longitude'], s['lines'], s['accessible'])
        )
        count += 1
        
    db.commit()
    print(f"✓ Successfully seeded {count} stations.")

def fetch_mta_entrances():
    """Fetch actual subway entrances from MTA's official dataset."""
    # MTA Subway Entrances and Exits dataset from data.ny.gov
    dataset_url = "https://data.ny.gov/resource/i9wp-a4ja.json"
    
    try:
        # Fetch all entrances with coordinates
        # Column names are entrance_latitude and entrance_longitude
        params = {
            '$limit': 10000,
            '$where': "entrance_latitude is not null and entrance_longitude is not null"
        }
        
        response = requests.get(dataset_url, params=params, timeout=120)
        if response.status_code == 200:
            data = response.json()
            print(f"✓ Fetched {len(data)} entrances from MTA dataset")
            return data
        else:
            print(f"⚠ MTA API returned status {response.status_code}")
            return []
    except Exception as e:
        print(f"✗ Error fetching MTA data: {e}")
        return []

def match_entrances_to_stations(mta_entrances, stations):
    """Match MTA entrance data to our station database."""
    from app.blueprints.route import calculate_distance
    
    # Create a mapping of station names (normalized) to station objects
    station_map = {}
    for station in stations:
        # Normalize station name for matching
        normalized_name = station['name'].lower().strip()
        # Remove common suffixes and normalize
        normalized_name = normalized_name.replace(' st', ' st').replace(' av', ' av').replace(' ave', ' av')
        normalized_name = normalized_name.replace('-', ' ').replace('  ', ' ')
        if normalized_name not in station_map:
            station_map[normalized_name] = []
        station_map[normalized_name].append(station)
    
    matched_entrances = []
    unmatched_count = 0
    
    for entrance in mta_entrances:
        # MTA dataset uses stop_name or constituent_station_name
        station_name = entrance.get('stop_name', '') or entrance.get('constituent_station_name', '')
        station_name = station_name.strip()
        if not station_name:
            continue
        
        lat = entrance.get('entrance_latitude')
        lon = entrance.get('entrance_longitude')
        
        try:
            lat = float(lat) if lat else None
            lon = float(lon) if lon else None
        except (ValueError, TypeError):
            continue
        
        if not lat or not lon:
            continue
        
        # Normalize MTA station name
        normalized_mta_name = station_name.lower().strip()
        normalized_mta_name = normalized_mta_name.replace(' st', ' st').replace(' av', ' av').replace(' ave', ' av')
        normalized_mta_name = normalized_mta_name.replace('-', ' ').replace('  ', ' ')
        
        # Try exact match first
        matched_station = None
        if normalized_mta_name in station_map:
            # If multiple stations with same name, find closest by distance
            candidates = station_map[normalized_mta_name]
            if len(candidates) == 1:
                matched_station = candidates[0]
            else:
                # Find closest by distance
                min_dist = float('inf')
                for candidate in candidates:
                    dist = calculate_distance(lat, lon, candidate['latitude'], candidate['longitude'])
                    if dist < 200 and dist < min_dist:
                        min_dist = dist
                        matched_station = candidate
        else:
            # Try fuzzy matching - find closest station by distance
            # Use more aggressive matching: prioritize distance, then name similarity
            min_distance = float('inf')
            best_match = None
            
            for station in stations:
                dist = calculate_distance(lat, lon, station['latitude'], station['longitude'])
                
                if dist < 250:  # Within 250m (increased threshold)
                    # Check name similarity
                    station_name_lower = station['name'].lower()
                    station_normalized = station_name_lower.replace(' st', ' st').replace(' av', ' av').replace(' ave', ' av')
                    station_normalized = station_normalized.replace('-', ' ').replace('  ', ' ')
                    
                    mta_words = set(normalized_mta_name.split())
                    station_words = set(station_normalized.split())
                    word_overlap = len(mta_words.intersection(station_words))
                    
                    # Very aggressive matching for accuracy:
                    # 1. Very close (<50m) - always match (entrance is definitely for this station)
                    # 2. Close (<100m) - always match (entrance is very likely for this station)
                    # 3. Reasonable distance (<150m) with any word overlap
                    # 4. Further (<200m) with 2+ word overlap
                    # 5. Even further (<250m) with 3+ word overlap or substring match
                    should_match = False
                    if dist < 100:  # Within 100m, always match (entrance is definitely for this station)
                        should_match = True
                    elif dist < 150 and word_overlap >= 1:
                        should_match = True
                    elif dist < 200 and word_overlap >= 2:
                        should_match = True
                    elif dist < 250 and (word_overlap >= 3 or normalized_mta_name in station_normalized or station_normalized in normalized_mta_name):
                        should_match = True
                    
                    if should_match and dist < min_distance:
                        min_distance = dist
                        matched_station = station
        
        if matched_station:
            # Get entrance description
            entrance_type = entrance.get('entrance_type', '').strip()
            entry_allowed = entrance.get('entry_allowed', '').strip()
            exit_allowed = entrance.get('exit_allowed', '').strip()
            
            description = entrance_type or "Entrance"
            if entry_allowed == 'YES' and exit_allowed != 'YES':
                description += " (Entry Only)"
            elif exit_allowed == 'YES' and entry_allowed != 'YES':
                description += " (Exit Only)"
            
            matched_entrances.append({
                'station_id': matched_station['id'],
                'latitude': lat,
                'longitude': lon,
                'description': description
            })
        else:
            unmatched_count += 1
    
    return matched_entrances, unmatched_count

def seed_station_entrances():
    """Seed database with actual station entrances from MTA's official dataset."""
    from app.models import Station
    import math
    
    db = get_db()
    
    # Get all stations
    all_stations = Station.get_all()
    print(f"Fetching entrances for {len(all_stations)} stations from MTA official dataset...")
    
    # Fetch MTA entrance data
    mta_entrances = fetch_mta_entrances()
    
    entrance_data = []
    stations_with_mta_data = set()
    
    if mta_entrances:
        # Match MTA entrances to our stations
        print("Matching MTA entrances to stations...")
        matched_entrances, unmatched = match_entrances_to_stations(mta_entrances, all_stations)
        
        print(f"✓ Matched {len(matched_entrances)} MTA entrances to stations")
        if unmatched > 0:
            print(f"  ⚠ {unmatched} MTA entrances couldn't be matched")
        
        # Group by station to track which stations have MTA data
        for entrance in matched_entrances:
            stations_with_mta_data.add(entrance['station_id'])
            entrance_data.append(entrance)
    
    # Find stations without entrances and add estimated ones
    stations_needing_entrances = []
    
    for station in all_stations:
        station_id = station['id']
        
        # Check if entrances already exist
        existing = db.execute(
            'SELECT COUNT(*) as count FROM station_entrances WHERE station_id = ?',
            (station_id,)
        ).fetchone()
        
        if existing['count'] > 0:
            continue  # Skip if already has entrances
        
        if station_id not in stations_with_mta_data:
            stations_needing_entrances.append(station)
    
    # Ensure every station has at least 2 entrances
    # First, check which stations need more entrances
    stations_needing_more = []
    for station in all_stations:
        station_id = station['id']
        existing_count = db.execute(
            'SELECT COUNT(*) as count FROM station_entrances WHERE station_id = ?',
            (station_id,)
        ).fetchone()['count']
        
        if existing_count < 2:
            stations_needing_more.append((station, existing_count))
    
    # Add estimated entrances for stations without MTA data or with < 2 entrances
    if stations_needing_entrances or stations_needing_more:
        print(f"\\nAdding entrances for stations needing them...")
        all_stations_to_process = set()
        for station in stations_needing_entrances:
            all_stations_to_process.add(station['id'])
        for station, count in stations_needing_more:
            all_stations_to_process.add(station['id'])
        
        for station in all_stations:
            if station['id'] not in all_stations_to_process:
                continue
                
            station_id = station['id']
            station_lat = station['latitude']
            station_lon = station['longitude']
            station_name = station['name']
            
            # Check how many entrances this station already has
            existing_count = db.execute(
                'SELECT COUNT(*) as count FROM station_entrances WHERE station_id = ?',
                (station_id,)
            ).fetchone()['count']
            
            # Create enough entrances to reach at least 2 total
            needed = max(2 - existing_count, 2)  # At least 2, or enough to reach 2
            
            R = 6371000
            offset_distances = [60, 90, 120]
            bearings = [0, 90, 180, 270]
            directions = ['North', 'East', 'South', 'West']
            
            for i in range(min(needed, len(offset_distances))):
                distance = offset_distances[i] / R
                bearing_rad = math.radians(bearings[i])
                
                lat_rad = math.radians(station_lat)
                lon_rad = math.radians(station_lon)
                
                new_lat_rad = math.asin(
                    math.sin(lat_rad) * math.cos(distance) +
                    math.cos(lat_rad) * math.sin(distance) * math.cos(bearing_rad)
                )
                
                new_lon_rad = lon_rad + math.atan2(
                    math.sin(bearing_rad) * math.sin(distance) * math.cos(lat_rad),
                    math.cos(distance) - math.sin(lat_rad) * math.sin(new_lat_rad)
                )
                
                entrance_lat = math.degrees(new_lat_rad)
                entrance_lon = math.degrees(new_lon_rad)
                description = f"{directions[i]} Entrance (Estimated)"
                
                entrance_data.append({
                    'station_id': station_id,
                    'latitude': entrance_lat,
                    'longitude': entrance_lon,
                    'description': description
                })
    
    # Insert all entrances (skip duplicates)
    print(f"\\nInserting {len(entrance_data)} entrances into database...")
    inserted_count = 0
    existing_coords = set()  # Track existing coordinates to avoid duplicates
    
    for entrance in entrance_data:
        # Check if this exact entrance already exists
        coord_key = (entrance['station_id'], round(entrance['latitude'], 5), round(entrance['longitude'], 5))
        if coord_key in existing_coords:
            continue
            
        existing = db.execute(
            'SELECT id FROM station_entrances WHERE station_id = ? AND ABS(latitude - ?) < 0.0001 AND ABS(longitude - ?) < 0.0001',
            (entrance['station_id'], entrance['latitude'], entrance['longitude'])
        ).fetchone()
        
        if not existing:
            db.execute(
                'INSERT INTO station_entrances (station_id, latitude, longitude, description) VALUES (?, ?, ?, ?)',
                (entrance['station_id'], entrance['latitude'], entrance['longitude'], entrance['description'])
            )
            existing_coords.add(coord_key)
            inserted_count += 1
    
    db.commit()
    
    # Second pass: Try to match remaining MTA entrances by pure distance (very aggressive)
    if mta_entrances:
        print(f"\\nSecond pass: Matching remaining MTA entrances by distance...")
        from app.blueprints.route import calculate_distance
        
        # Get all existing entrance coordinates
        existing_entrance_coords = set()
        for row in db.execute('SELECT station_id, ROUND(latitude, 5) as lat, ROUND(longitude, 5) as lon FROM station_entrances').fetchall():
            existing_entrance_coords.add((row['station_id'], row['lat'], row['lon']))
        
        additional_matches = 0
        for entrance in mta_entrances:
            lat = entrance.get('entrance_latitude')
            lon = entrance.get('entrance_longitude')
            
            try:
                lat, lon = float(lat), float(lon)
            except:
                continue
            
            # Check if this entrance is already in database
            coord_rounded = (round(lat, 5), round(lon, 5))
            if any(abs(existing[1] - lat) < 0.0001 and abs(existing[2] - lon) < 0.0001 
                   for existing in existing_entrance_coords):
                continue
            
            # Find closest station within 150m (pure distance match)
            min_dist = float('inf')
            closest = None
            for station in all_stations:
                dist = calculate_distance(lat, lon, station['latitude'], station['longitude'])
                if dist < 150 and dist < min_dist:
                    min_dist = dist
                    closest = station
            
            if closest:
                entrance_type = entrance.get('entrance_type', '').strip() or "Entrance"
                entry_allowed = entrance.get('entry_allowed', '').strip()
                exit_allowed = entrance.get('exit_allowed', '').strip()
                
                description = entrance_type
                if entry_allowed == 'YES' and exit_allowed != 'YES':
                    description += " (Entry Only)"
                elif exit_allowed == 'YES' and entry_allowed != 'YES':
                    description += " (Exit Only)"
                
                db.execute(
                    'INSERT INTO station_entrances (station_id, latitude, longitude, description) VALUES (?, ?, ?, ?)',
                    (closest['id'], lat, lon, description)
                )
                existing_entrance_coords.add((closest['id'], round(lat, 5), round(lon, 5)))
                additional_matches += 1
        
        db.commit()
        if additional_matches > 0:
            print(f"  ✓ Added {additional_matches} additional MTA entrances via distance matching")
    
    # Final statistics
    total_entrances = db.execute('SELECT COUNT(*) as count FROM station_entrances').fetchone()
    stations_with_entrances = db.execute('''
        SELECT COUNT(DISTINCT station_id) as count FROM station_entrances
    ''').fetchone()
    
    mta_entrance_count = db.execute('''
        SELECT COUNT(*) as count FROM station_entrances 
        WHERE description NOT LIKE '%Estimated%'
    ''').fetchone()
    
    print(f"\\n✓ Complete! Total entrances: {total_entrances['count']}")
    print(f"  - Stations with entrances: {stations_with_entrances['count']}/{len(all_stations)}")
    print(f"  - MTA official entrances: {mta_entrance_count['count']}")
    print(f"  - Estimated entrances: {total_entrances['count'] - mta_entrance_count['count']}")
    print(f"  - New entrances inserted: {inserted_count}")
