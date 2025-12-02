from flask import Blueprint, render_template, request, jsonify
import requests
import os

bp = Blueprint("route", __name__)

# OpenRouteService API (free tier available)
ORS_API_KEY = os.getenv("ORS_API_KEY", "")

# Google Maps Directions API (for route comparison)
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# Routing Efficiency Constants
# Thresholds for detecting inefficient routes (e.g. bad snapping to tunnels/bridges)
ROUTE_EFFICIENCY_CHECK_DISTANCE = 800      # Only check for inefficiency if straight-line distance is < 800m
ROUTE_EFFICIENCY_THRESHOLD_RATIO = 2.0     # Route is inefficient if it's > 2.0x the straight-line distance
HEURISTIC_FALLBACK_FACTOR = 1.3            # Factor to apply to straight-line distance for heuristic fallback

def geocode_address(address):
    """Geocode an address to coordinates using Nominatim."""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": address,
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
            "bounded": 1,
            "viewbox": "-74.3,40.4,-73.7,41.0",  # NYC area bounding box
            "countrycodes": "us"
        }
        headers = {"User-Agent": "AlwaysOnTime/1.0"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data:
                full_address = data[0]["display_name"]
                return {
                    "latitude": float(data[0]["lat"]),
                    "longitude": float(data[0]["lon"]),
                    "display_name": shorten_address(full_address)
                }
    except Exception as e:
        logger.error(f"Geocoding error: {e}")
    return None

def shorten_address(address):
    """Shorten address by removing county, state, country, zip code, redundant city names, and villages."""
    if not address:
        return address
    
    # Split by commas and filter out unwanted parts
    parts = [part.strip() for part in address.split(',')]
    filtered_parts = []
    
    for part in parts:
        part_lower = part.lower()
        # Skip county, state, country, zip codes, and villages
        if any(skip in part_lower for skip in ['county', 'united states', 'usa', 'new york state', 'ny state', 'city of new york', 'village', 'yard']):
            continue
        # Skip zip codes (5 digits)
        if part.strip().isdigit() and len(part.strip()) == 5:
            continue
        # Skip if it's just "New York" (state) - we'll keep borough names
        if part_lower == 'new york':
            # This is likely the state name
            continue
        filtered_parts.append(part)
    
    # Join back together
    return ', '.join(filtered_parts)

def reverse_geocode(lat, lon):
    """Reverse geocode coordinates to an address using Nominatim."""
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "lat": lat,
            "lon": lon,
            "format": "json",
            "addressdetails": 1
        }
        headers = {"User-Agent": "AlwaysOnTime/1.0"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and "display_name" in data:
                address = data["display_name"]
                return shorten_address(address)
    except Exception as e:
        logger.error(f"Reverse geocoding error: {e}")
    return None

@bp.get("/api/route/search")
def search_locations():
    """Search for locations with autocomplete using Photon API (faster/fuzzy)."""
    query = request.args.get('q', '')
    if not query or len(query) < 2:
        return jsonify({"results": []})
    
    try:
        # Use Photon API (based on OSM, supports fuzzy search)
        url = "https://photon.komoot.io/api/"
        params = {
            "q": query,
            "limit": 5,
            "bbox": "-74.3,40.4,-73.7,41.0",  # NYC area bounding box
            "lang": "en"
        }
        
        # Add a small timeout to prevent hanging
        response = requests.get(url, params=params, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            results = []
            
            for feature in data.get("features", []):
                props = feature.get("properties", {})
                coords = feature.get("geometry", {}).get("coordinates", [0, 0])
                
                # Build a clean display name
                name = props.get("name", "")
                street = props.get("street", "")
                housenumber = props.get("housenumber", "")
                city = props.get("city", props.get("town", props.get("district", "")))
                
                # Construct address part
                address_parts = []
                if housenumber and street:
                    address_parts.append(f"{housenumber} {street}")
                elif street:
                    address_parts.append(street)
                
                if city and city != name: # Avoid duplication if name is the city
                    address_parts.append(city)
                
                # Format: "Name" or "Address"
                if name:
                    display_name = name
                    if address_parts:
                        display_name += f", {', '.join(address_parts)}"
                else:
                    display_name = ", ".join(address_parts) if address_parts else "Unknown Location"
                
                # Fallback if empty
                if not display_name or display_name == "Unknown Location":
                    # Try to use formatted address if available or just raw properties
                    display_name = props.get("formatted", "") or f"{coords[1]:.4f}, {coords[0]:.4f}"

                results.append({
                    "display_name": display_name,
                    "name": name, # Send raw name for UI highlighting
                    "address": ", ".join(address_parts), # Send raw address for UI
                    "latitude": float(coords[1]),
                    "longitude": float(coords[0])
                })
                
            return jsonify({"results": results})
            
    except Exception as e:
        print(f"Search error: {e}")
    
    return jsonify({"results": []})


# Cache for walking routes (key -> (route_data, timestamp))
_walking_route_cache = {}
_walking_cache_timeout = 3600  # Cache for 1 hour

def get_walking_route(start_lat, start_lon, end_lat, end_lon):
    """Get walking route between two points using OpenRouteService."""
    import time
    
    # Create cache key (rounded to ~10m precision to increase hit rate)
    key = f"{round(start_lat, 4)},{round(start_lon, 4)}-{round(end_lat, 4)},{round(end_lon, 4)}"
    
    if key in _walking_route_cache:
        data, timestamp = _walking_route_cache[key]
        if time.time() - timestamp < _walking_cache_timeout:
            return data
            
    # Helper to cache and return
    def cache_and_return(result):
        if result:
            _walking_route_cache[key] = (result, time.time())
        return result

    # Helper to generate candidate points (approx 30m offsets)
    def _get_candidate_points(lat, lon):
        offsets = [
            (0, 0),          # Original
            (0.0003, 0),     # North ~30m
            (-0.0003, 0),    # South ~30m
            (0, 0.0003),     # East ~30m
            (0, -0.0003)     # West ~30m
        ]
        return [(lat + lat_off, lon + lon_off) for lat_off, lon_off in offsets]

    # Internal function to perform the actual API request
    def _request_route(s_lat, s_lon, e_lat, e_lon):
        # Use OpenRouteService if API key is available
        if ORS_API_KEY:
            try:
                url = "https://api.openrouteservice.org/v2/directions/foot-walking"
                headers = {"Authorization": ORS_API_KEY}
                params = {
                    "api_key": ORS_API_KEY,
                    "start": f"{s_lon},{s_lat}",
                    "end": f"{e_lon},{e_lat}"
                }
                response = requests.get(url, headers=headers, params=params, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if "features" in data and len(data["features"]) > 0:
                        route = data["features"][0]
                        geometry = route["geometry"]["coordinates"]
                        route_coords = [[coord[1], coord[0]] for coord in geometry]
                        properties = route["properties"]
                        return {
                            "distance": properties.get("segments", [{}])[0].get("distance", 0),
                            "duration": int(properties.get("segments", [{}])[0].get("duration", 0) / 60),
                            "route": route_coords
                        }
            except Exception:
                pass
        
        # Try OSRM public API as fallback
        try:
            url = f"http://router.project-osrm.org/route/v1/walking/{s_lon},{s_lat};{e_lon},{e_lat}"
            params = {"overview": "full", "geometries": "geojson"}
            response = requests.get(url, params=params, timeout=3)
            if response.status_code == 200:
                data = response.json()
                if "routes" in data and len(data["routes"]) > 0:
                    route = data["routes"][0]
                    geometry = route["geometry"]["coordinates"]
                    route_coords = [[coord[1], coord[0]] for coord in geometry]
                    distance_meters = route["distance"]
                    return {
                        "distance": distance_meters,
                        "duration": int((distance_meters / 1.25) / 60),
                        "route": route_coords
                    }
        except Exception:
            pass
        return None

    # Helper to validate and return route, with multi-point retry
    def validate_and_return(result, depth=0):
        if not result:
            return None
            
        # Calculate straight-line distance
        dist_straight = calculate_distance(start_lat, start_lon, end_lat, end_lon)
        dist_routed = result['distance']
        
        # Sanity check: If routed distance is significantly larger than straight-line
        # Relaxed thresholds: < ROUTE_EFFICIENCY_CHECK_DISTANCE and > ROUTE_EFFICIENCY_THRESHOLD_RATIO detour
        is_inefficient = dist_straight < ROUTE_EFFICIENCY_CHECK_DISTANCE and dist_routed > dist_straight * ROUTE_EFFICIENCY_THRESHOLD_RATIO
        
        if is_inefficient and depth == 0:
            print(f"Route inefficient (routed {dist_routed}m vs straight {dist_straight}m). Attempting multi-point routing...")
            
            # Generate candidates
            start_candidates = _get_candidate_points(start_lat, start_lon)
            end_candidates = _get_candidate_points(end_lat, end_lon)
            
            best_route = result
            min_dist = dist_routed
            
            # Strategy: Try shifting Start first (common issue), then End
            # We don't do all 5x5=25 combinations to save time/quota.
            # Just try: Original->Neighbors(End) and Neighbors(Start)->Original
            
            candidates_to_try = []
            # 1. Try shifting End point (4 attempts)
            for i in range(1, 5):
                candidates_to_try.append((start_lat, start_lon, end_candidates[i][0], end_candidates[i][1]))
            # 2. Try shifting Start point (4 attempts)
            for i in range(1, 5):
                candidates_to_try.append((start_candidates[i][0], start_candidates[i][1], end_lat, end_lon))
                
            for s_lat, s_lon, e_lat, e_lon in candidates_to_try:
                alt_result = _request_route(s_lat, s_lon, e_lat, e_lon)
                if alt_result:
                    # Add penalty for offset distance (approx distance from original point)
                    # But for now, just comparing routed distance is usually enough
                    if alt_result['distance'] < min_dist:
                        min_dist = alt_result['distance']
                        best_route = alt_result
            
            # Check if the best alternative is acceptable
            if min_dist < dist_straight * ROUTE_EFFICIENCY_THRESHOLD_RATIO:
                print(f"Found better route: {min_dist}m")
                return cache_and_return(best_route)
            else:
                print("Multi-point routing failed to find efficient path. Falling back to heuristic.")

        # If still inefficient after retries (or if depth > 0), fallback to heuristic
        if is_inefficient:
             heuristic_dist = dist_straight * HEURISTIC_FALLBACK_FACTOR
             return cache_and_return({
                "distance": heuristic_dist,
                "duration": int((heuristic_dist / 1.35) / 60),
                "route": [[start_lat, start_lon], [end_lat, end_lon]]
            })
            
        return cache_and_return(result)

    # Initial Request
    initial_result = _request_route(start_lat, start_lon, end_lat, end_lon)
    return validate_and_return(initial_result)

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points using Haversine formula (returns meters)."""
    import math
    R = 6371000  # Earth radius in meters
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def find_nearest_entrance(user_lat, user_lon, station_id, station_lat=None, station_lon=None, check_walking=True):
    """
    Find the nearest entrance location to a station.
    First tries to find actual entrances from database, falls back to heuristic if none exist.
    check_walking: If True, verifies actual walking distance (slower). If False, uses straight-line (faster).
    """
    from app.models import StationEntrance
    
    # Try to find actual entrance from database
    entrances = StationEntrance.get_by_station(station_id)
    
    if entrances:
        # Calculate straight-line distance for all entrances
        entrance_dists = []
        for ent in entrances:
            dist = calculate_distance(user_lat, user_lon, ent['latitude'], ent['longitude'])
            entrance_dists.append((ent, dist))
        
        # Sort by straight-line distance
        entrance_dists.sort(key=lambda x: x[1])
        
        # If we don't need to check walking distance, just return the closest one
        if not check_walking:
            best_entrance = entrance_dists[0][0]
            return best_entrance['latitude'], best_entrance['longitude'], best_entrance['description'] if best_entrance['description'] else 'Entrance'
        
        # Check top 3 closest entrances for actual walking distance
        # This avoids selecting an entrance that is physically close but requires a long detour
        best_entrance = None
        min_walking_dist = float('inf')
        
        # Optimization: Check up to 3 candidates
        candidates = entrance_dists[:3]
        
        for ent, straight_dist in candidates:
            # Get actual walking route
            route = get_walking_route(user_lat, user_lon, ent['latitude'], ent['longitude'])
            
            if route:
                walking_dist = route['distance']
                
                # Sanity check: If walking distance is unreasonable (> 3x straight-line)
                # and we are close (< 300m), assume router failed to snap and use heuristic
                if straight_dist < 300 and walking_dist > straight_dist * 3:
                    # Use heuristic: straight-line * 1.4 (typical urban penalty)
                    walking_dist = straight_dist * 1.4
                
                # Optimization: If walking distance is very close to straight-line (within 1.5x),
                # it's likely a direct path, so just take it immediately
                if walking_dist < straight_dist * 1.5:
                    return ent['latitude'], ent['longitude'], ent['description'] if ent['description'] else 'Entrance'
                
                if walking_dist < min_walking_dist:
                    min_walking_dist = walking_dist
                    best_entrance = ent
            else:
                # If routing fails, fall back to straight-line distance
                if straight_dist < min_walking_dist:
                    min_walking_dist = straight_dist
                    best_entrance = ent
        
        if best_entrance:
            return best_entrance['latitude'], best_entrance['longitude'], best_entrance['description'] if best_entrance['description'] else 'Entrance'
        
        # Fallback to closest by straight line if something went wrong
        fallback = entrance_dists[0][0]
        return fallback['latitude'], fallback['longitude'], fallback['description'] if fallback['description'] else 'Entrance'
    
    # Fallback: use heuristic if no entrances in database
    if station_lat is None or station_lon is None:
        from app.models import Station
        station = Station.get_by_id(station_id)
        if not station:
            return None, None, None
        station_lat = station['latitude']
        station_lon = station['longitude']
    
    import math
    
    # Calculate bearing from station to user
    lat1_rad = math.radians(station_lat)
    lat2_rad = math.radians(user_lat)
    delta_lon = math.radians(user_lon - station_lon)
    
    y = math.sin(delta_lon) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon)
    bearing = math.atan2(y, x)
    
    # Distance from station center to approximate entrance location
    # Most NYC subway entrances are within 50-150m of station center
    # Use 75m as a reasonable average
    entrance_offset = 75  # meters
    
    # Calculate entrance location: move from station center towards user by entrance_offset
    R = 6371000  # Earth radius in meters
    d = entrance_offset / R  # Distance in radians
    
    lat_entrance_rad = math.asin(
        math.sin(lat1_rad) * math.cos(d) +
        math.cos(lat1_rad) * math.sin(d) * math.cos(bearing)
    )
    
    lon_entrance_rad = math.radians(station_lon) + math.atan2(
        math.sin(bearing) * math.sin(d) * math.cos(lat1_rad),
        math.cos(d) - math.sin(lat1_rad) * math.sin(lat_entrance_rad)
    )
    
    # Generate a heuristic description based on bearing
    bearing_deg = math.degrees(bearing) % 360
    directions = ["North", "Northeast", "East", "Southeast", "South", "Southwest", "West", "Northwest"]
    idx = int((bearing_deg + 22.5) / 45) % 8
    direction_str = directions[idx]
    description = f"{direction_str} side of station (Estimated)"
    
    return math.degrees(lat_entrance_rad), math.degrees(lon_entrance_rad), description

def get_actual_subway_time(origin_station, dest_station, train_route_id, origin_arrival_time, trip_id=None):
    """
    Get actual subway travel time using MTA real-time data.
    Finds the same train's arrival time at destination station by matching trip_id.
    Returns actual time in minutes, or None if not found.
    """
    from app.blueprints.status import _fetch_gtfs_feed
    from datetime import datetime
    
    if not train_route_id:
        return None
    
    # Map route to feed
    line_to_feed = {
        '1': 'nyct%2Fgtfs', '2': 'nyct%2Fgtfs', '3': 'nyct%2Fgtfs',
        '4': 'nyct%2Fgtfs', '5': 'nyct%2Fgtfs', '6': 'nyct%2Fgtfs', 'S': 'nyct%2Fgtfs',
        'A': 'nyct%2Fgtfs-ace', 'C': 'nyct%2Fgtfs-ace', 'E': 'nyct%2Fgtfs-ace',
        'B': 'nyct%2Fgtfs-bdfm', 'D': 'nyct%2Fgtfs-bdfm', 'F': 'nyct%2Fgtfs-bdfm', 'M': 'nyct%2Fgtfs-bdfm',
        'G': 'nyct%2Fgtfs-g',
        'J': 'nyct%2Fgtfs-jz', 'Z': 'nyct%2Fgtfs-jz',
        'L': 'nyct%2Fgtfs-l',
        'N': 'nyct%2Fgtfs-nqrw', 'Q': 'nyct%2Fgtfs-nqrw', 'R': 'nyct%2Fgtfs-nqrw', 'W': 'nyct%2Fgtfs-nqrw'
    }
    
    train_line = train_route_id[0] if train_route_id else None
    if not train_line or train_line not in line_to_feed:
        return None
    
    feed_path = line_to_feed[train_line]
    feed = _fetch_gtfs_feed(feed_path)
    
    if not feed:
        return None
    
    # Convert origin arrival time to timestamp
    if isinstance(origin_arrival_time, int):
        # It's minutes away, convert to timestamp
        now = datetime.now().timestamp()
        origin_timestamp = now + (origin_arrival_time * 60)
    else:
        # Assume it's already a timestamp
        origin_timestamp = origin_arrival_time
    
    # Get destination station lines to verify the train goes there
    dest_lines = set()
    if dest_station and 'lines' in dest_station and dest_station['lines']:
        dest_lines = {line.strip() for line in dest_station['lines'].split(',')}
    
    # If we have trip_id, use it to find the exact same train
    if trip_id:
        for entity in feed.entity:
            if entity.HasField('trip_update'):
                trip_update = entity.trip_update
                
                # Check if this is the same trip
                current_trip_id = None
                route_id = None
                if trip_update.HasField('trip'):
                    if trip_update.trip.HasField('trip_id'):
                        current_trip_id = trip_update.trip.trip_id
                    if trip_update.trip.HasField('route_id'):
                        route_id = trip_update.trip.route_id
                
                # Must match both trip_id and route_id
                if current_trip_id != trip_id or route_id != train_route_id:
                    continue
                
                # Find origin stop (within 5 min of expected time) and destination stop (after origin)
                origin_stop_time = None
                dest_stop_time = None
                
                for stop_update in trip_update.stop_time_update:
                    if stop_update.HasField('arrival') and stop_update.arrival.HasField('time'):
                        arrival_timestamp = stop_update.arrival.time
                        
                        # Check if this could be the origin stop (within 5 minutes)
                        time_diff = abs(arrival_timestamp - origin_timestamp)
                        if time_diff < 300:  # Within 5 minutes
                            if origin_stop_time is None or time_diff < abs(origin_stop_time - origin_timestamp):
                                origin_stop_time = arrival_timestamp
                        
                        # Check if this could be destination (after origin, reasonable time)
                        if arrival_timestamp > origin_timestamp:
                            # Only consider stops that are significantly after origin (at least 2 min)
                            if arrival_timestamp > (origin_timestamp + 120):  # At least 2 minutes later
                                if dest_stop_time is None or arrival_timestamp < dest_stop_time:
                                    dest_stop_time = arrival_timestamp
                
                # If we found both stops, calculate the time difference
                if origin_stop_time and dest_stop_time:
                    subway_time_minutes = int((dest_stop_time - origin_stop_time) / 60)
                    if subway_time_minutes > 0 and subway_time_minutes < 120:  # Reasonable range
                        return subway_time_minutes
    
    # Fallback: Find any train on this route that has stops after origin time
    # This is less accurate but better than nothing
    for entity in feed.entity:
        if entity.HasField('trip_update'):
            trip_update = entity.trip_update
            
            route_id = None
            if trip_update.HasField('trip') and trip_update.trip.HasField('route_id'):
                route_id = trip_update.trip.route_id
            
            if route_id != train_route_id:
                continue
            
            # Look for stops after origin time
            stops_after_origin = []
            for stop_update in trip_update.stop_time_update:
                if stop_update.HasField('arrival') and stop_update.arrival.HasField('time'):
                    arrival_timestamp = stop_update.arrival.time
                    # Only consider stops significantly after origin (at least 2 min)
                    if arrival_timestamp > (origin_timestamp + 120):
                        stops_after_origin.append(arrival_timestamp)
            
            if stops_after_origin:
                # Use the first stop after origin as destination estimate
                first_stop_after = min(stops_after_origin)
                subway_time_minutes = int((first_stop_after - origin_timestamp) / 60)
                if subway_time_minutes > 0 and subway_time_minutes < 120:
                    return subway_time_minutes
    
    return None

def _build_subway_path(origin_station, dest_station, intermediate_stations):
    """
    Build a curved path through intermediate stations for subway route visualization.
    Returns a list of coordinate pairs [lat, lon] for drawing the path.
    """
    path = []
    
    # Start with origin station
    path.append([origin_station['latitude'], origin_station['longitude']])
    
    # Add intermediate stations in order
    if intermediate_stations:
        for station in intermediate_stations:
            if 'coordinates' in station:
                path.append(station['coordinates'])
            elif 'latitude' in station and 'longitude' in station:
                path.append([station['latitude'], station['longitude']])
    
    # End with destination station
    path.append([dest_station['latitude'], dest_station['longitude']])
    
    # If we have intermediate stations, create a smoother curve
    # Otherwise, return a simple path (will be curved by Leaflet)
    if len(path) > 2:
        # Create a smoother curve by adding waypoints
        smoothed_path = []
        for i in range(len(path) - 1):
            smoothed_path.append(path[i])
            # Add a midpoint for smoother curves
            if i < len(path) - 1:
                mid_lat = (path[i][0] + path[i + 1][0]) / 2
                mid_lon = (path[i][1] + path[i + 1][1]) / 2
                # Slightly offset the midpoint to create a curve
                import math
                # Calculate perpendicular offset for curve
                dx = path[i + 1][1] - path[i][1]
                dy = path[i + 1][0] - path[i][0]
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0:
                    # Offset perpendicular to the line (creates a slight curve)
                    offset = 0.0001  # Small offset for visual curve
                    mid_lat += offset * (dx / dist)
                    mid_lon -= offset * (dy / dist)
                smoothed_path.append([mid_lat, mid_lon])
        smoothed_path.append(path[-1])
        return smoothed_path
    
    return path

def find_intermediate_stations(origin_station, dest_station, train_line):
    """
    Find intermediate stations between origin and destination on the same line.
    Uses GTFS-realtime data to get actual station order from trip updates.
    Returns a list of station dictionaries with coordinates for path drawing.
    """
    from app.models import Station
    from app.blueprints.status import _fetch_gtfs_feed
    
    if not train_line:
        return []
    
    # Map line to feed
    line_to_feed = {
        '1': 'nyct%2Fgtfs', '2': 'nyct%2Fgtfs', '3': 'nyct%2Fgtfs',
        '4': 'nyct%2Fgtfs', '5': 'nyct%2Fgtfs', '6': 'nyct%2Fgtfs', 'S': 'nyct%2Fgtfs',
        'A': 'nyct%2Fgtfs-ace', 'C': 'nyct%2Fgtfs-ace', 'E': 'nyct%2Fgtfs-ace',
        'B': 'nyct%2Fgtfs-bdfm', 'D': 'nyct%2Fgtfs-bdfm', 'F': 'nyct%2Fgtfs-bdfm', 'M': 'nyct%2Fgtfs-bdfm',
        'G': 'nyct%2Fgtfs-g',
        'J': 'nyct%2Fgtfs-jz', 'Z': 'nyct%2Fgtfs-jz',
        'L': 'nyct%2Fgtfs-l',
        'N': 'nyct%2Fgtfs-nqrw', 'Q': 'nyct%2Fgtfs-nqrw', 'R': 'nyct%2Fgtfs-nqrw', 'W': 'nyct%2Fgtfs-nqrw'
    }
    
    feed_path = line_to_feed.get(train_line)
    if not feed_path:
        return []
    
    # Try to get actual station sequence from GTFS-realtime
    feed = _fetch_gtfs_feed(feed_path)
    if feed:
        # Get all stations on this line
        all_stations = Station.get_all()
        station_by_name = {s['name'].lower(): s for s in all_stations}
        
        # Find a trip that goes through both origin and destination
        origin_name_lower = origin_station['name'].lower()
        dest_name_lower = dest_station['name'].lower()
        
        for entity in feed.entity:
            if entity.HasField('trip_update'):
                trip_update = entity.trip_update
                
                # Check route_id matches
                if trip_update.HasField('trip') and trip_update.trip.HasField('route_id'):
                    route_id = trip_update.trip.route_id
                    if route_id and route_id[0] != train_line:
                        continue
                
                # Get stop sequence
                stop_sequence = []
                origin_found = False
                dest_found = False
                origin_idx = -1
                dest_idx = -1
                last_station_name = None
                
                for stop_update in trip_update.stop_time_update:
                    if stop_update.HasField('stop_id'):
                        stop_id = stop_update.stop_id
                        # Try to match stop_id to station name
                        # Stop IDs often contain station names or identifiers
                        for station_name, station in station_by_name.items():
                            if (stop_id.lower() in station_name or 
                                station_name.replace(' ', '').replace('-', '').lower() in stop_id.lower()):
                                
                                # Deduplicate: Don't add same station twice in a row
                                if last_station_name and station['name'] == last_station_name:
                                    break
                                
                                stop_sequence.append({
                                    'stop_id': stop_id,
                                    'station_name': station['name'],
                                    'station': station,
                                    'sequence': len(stop_sequence)
                                })
                                last_station_name = station['name']
                                
                                # Check if this is origin or destination
                                if station_name == origin_name_lower:
                                    origin_found = True
                                    origin_idx = len(stop_sequence) - 1
                                if station_name == dest_name_lower:
                                    dest_found = True
                                    dest_idx = len(stop_sequence) - 1
                                break
            
                # If we found both origin and destination in sequence
                if origin_found and dest_found and origin_idx >= 0 and dest_idx >= 0:
                    # Get stations between origin and destination
                    # INCLUDE the destination (dest_idx + 1)
                    if origin_idx < dest_idx:
                        # Normal direction
                        intermediate_stops = stop_sequence[origin_idx + 1:dest_idx + 1]
                    else:
                        # Reverse direction
                        # If origin is after dest, we want stops from dest_idx up to (but not including) origin_idx
                        intermediate_stops = stop_sequence[dest_idx:origin_idx]
                        intermediate_stops.reverse() # Reverse to maintain logical order from origin to dest
                    
                    # Convert to our format
                    intermediate = []
                    for stop in intermediate_stops:
                        station = stop['station']
                        intermediate.append({
                            'name': station['name'],
                            'id': station['id'],
                            'latitude': station['latitude'],
                            'longitude': station['longitude'],
                            'coordinates': [station['latitude'], station['longitude']]
                        })
                    
                    if intermediate:
                        return intermediate[:15]  # Limit to 15 stations
    
    # Fallback: Use geographic heuristic if GTFS data doesn't work
    all_stations = Station.get_all()
    line_stations = []
    for station in all_stations:
        if station['lines']:
            station_lines = [line.strip() for line in station['lines'].split(',')]
            if train_line in station_lines:
                line_stations.append(station)
    
    if len(line_stations) < 2:
        return []
    
    # Simple geographic ordering: sort by distance from origin along the line
    import math
    intermediate = []
    origin_id = origin_station['id']
    dest_id = dest_station['id']
    
    for station in line_stations:
        if station['id'] == origin_id or station['id'] == dest_id:
            continue
        
        dist_to_origin = calculate_distance(
            origin_station['latitude'], origin_station['longitude'],
            station['latitude'], station['longitude']
        )
        dist_to_dest = calculate_distance(
            dest_station['latitude'], dest_station['longitude'],
            station['latitude'], station['longitude']
        )
        total_dist = calculate_distance(
            origin_station['latitude'], origin_station['longitude'],
            dest_station['latitude'], dest_station['longitude']
        )
        
        # Check if station is between origin and dest
        if dist_to_origin < total_dist and dist_to_dest < total_dist:
            path_deviation = (dist_to_origin + dist_to_dest) - total_dist
            if path_deviation < 1000:  # Within 1km of direct path
                intermediate.append({
                    'name': station['name'],
                    'distance_from_origin': dist_to_origin,
                    'id': station['id'],
                    'latitude': station['latitude'],
                    'longitude': station['longitude'],
                    'coordinates': [station['latitude'], station['longitude']]
                })
    
    intermediate.sort(key=lambda x: x['distance_from_origin'])
    return intermediate[:10]

def find_nearest_stations(lat, lon, limit=15, max_distance=2500):
    """Find nearest subway stations to given coordinates (returns multiple options)."""
    from app.models import Station
    
    stations = Station.get_all()
    if not stations:
        return []
    
    # Deduplicate stations by name and coordinates (keep first occurrence)
    seen_stations = {}
    unique_stations = []
    for station in stations:
        # Create unique key from name and coordinates (rounded to 4 decimal places)
        key = (station['name'], round(station['latitude'], 4), round(station['longitude'], 4))
        if key not in seen_stations:
            seen_stations[key] = station
            unique_stations.append(station)
    
    # First pass: Calculate straight-line distance to station center for ALL stations
    # This avoids calling find_nearest_entrance (which triggers API calls) for far-away stations
    initial_candidates = []
    for station in unique_stations:
        dist = calculate_distance(lat, lon, station['latitude'], station['longitude'])
        if dist <= max_distance * 1.5:  # Add buffer for initial filter
            initial_candidates.append((station, dist))
    
    # Sort by straight-line distance and take top candidates (e.g., 20)
    initial_candidates.sort(key=lambda x: x[1])
    top_candidates = initial_candidates[:20]
    
    station_distances = []
    
    # Second pass: Refine distance using actual entrances for top candidates only
    for station, _ in top_candidates:
        # Use entrance location for distance calculation (more accurate)
        # Pass check_walking=False to avoid expensive API calls - straight line to entrance is good enough for sorting
        entrance_lat, entrance_lon, _ = find_nearest_entrance(
            lat, lon, station['id'], station['latitude'], station['longitude'], check_walking=False
        )
        if entrance_lat is None or entrance_lon is None:
            # Fallback to station center if no entrance found
            entrance_lat, entrance_lon = station['latitude'], station['longitude']
        
        # We still use straight-line distance here for speed, but to the ENTRANCE
        # (find_nearest_entrance already did the heavy lifting of finding the best entrance)
        distance = calculate_distance(lat, lon, entrance_lat, entrance_lon)
        
        if distance <= max_distance:
            station_distances.append((station, distance))
    
    # Sort by refined distance and return top N
    station_distances.sort(key=lambda x: x[1])
    return [(station, dist) for station, dist in station_distances[:limit]]

# Cache for train frequency analysis (station_id -> frequency_data)
_frequency_cache = {}
_cache_timeout = 60  # Cache for 60 seconds

def analyze_train_frequency(station_id, station_lines, walk_to_time):
    """
    Analyze train frequency at a station to determine safety score.
    Uses caching to avoid repeated API calls.
    Returns: (avg_gap, max_gap, frequency_score)
    """
    from app.blueprints.station import _parse_next_arrivals
    from time import time
    
    # Check cache first
    cache_key = f"{station_id}_{station_lines}"
    if cache_key in _frequency_cache:
        cached_data, cached_time = _frequency_cache[cache_key]
        if time() - cached_time < _cache_timeout:
            # Use cached data but adjust for walk_time if needed
            avg_gap, max_gap, frequency_score = cached_data
            return avg_gap, max_gap, frequency_score
    
    # Fetch fresh data
    arrivals = _parse_next_arrivals(station_lines)
    
    if len(arrivals) < 2:
        # Not enough data, use heuristic based on number of lines
        num_lines = len(station_lines.split(',')) if station_lines else 1
        # More lines = better frequency (rough heuristic)
        avg_gap = max(5, 15 - num_lines * 2)
        max_gap = avg_gap * 2
        frequency_score = avg_gap * 2 + max_gap * 3
        result = (avg_gap, max_gap, frequency_score)
        _frequency_cache[cache_key] = (result, time())
        return result
    
    # Filter arrivals that are relevant (after walk time)
    relevant_arrivals = [a for a in arrivals if a.get('minutes_away', 0) >= walk_to_time]
    
    if len(relevant_arrivals) < 2:
        # Use all arrivals if not enough after walk time
        relevant_arrivals = arrivals[:5]
    
    # Calculate gaps between consecutive trains
    gaps = []
    for i in range(len(relevant_arrivals) - 1):
        gap = relevant_arrivals[i + 1]['minutes_away'] - relevant_arrivals[i]['minutes_away']
        if gap > 0:
            gaps.append(gap)
    
    if not gaps:
        # Fallback to heuristic
        num_lines = len(station_lines.split(',')) if station_lines else 1
        avg_gap = max(5, 15 - num_lines * 2)
        max_gap = avg_gap * 2
        frequency_score = avg_gap * 2 + max_gap * 3
        result = (avg_gap, max_gap, frequency_score)
        _frequency_cache[cache_key] = (result, time())
        return result
    
    avg_gap = sum(gaps) / len(gaps)
    max_gap = max(gaps)
    
    # Frequency score: lower is better (more frequent = lower score)
    frequency_score = avg_gap * 2 + max_gap * 3
    
    result = (avg_gap, max_gap, frequency_score)
    _frequency_cache[cache_key] = (result, time())
    return result

def estimate_subway_time_improved(origin_station, dest_station, shared_lines):
    """
    Improved subway time estimation using station count and actual network topology.
    Returns estimated time in minutes.
    """
    # First, try to get actual station count between origin and dest
    if shared_lines:
        # For direct routes, find intermediate stations to get accurate count
        train_line = list(shared_lines)[0] if shared_lines else None
        if train_line:
            intermediate = find_intermediate_stations(origin_station, dest_station, train_line)
            station_count = len(intermediate)
            
            # NYC subway average: ~1.5-2.5 min per station (including stops)
            # Express trains: ~1.2 min per station
            # Local trains: ~2.0 min per station
            # Use 1.8 min per station as average (more realistic)
            if station_count > 0:
                # We have intermediate stations, use accurate count
                subway_time = (station_count + 1) * 1.8  # +1 for the destination stop, 1.8 min per station
                # Add 0.5 min for initial boarding/acceleration
                subway_time += 0.5
                return max(4, int(subway_time))  # Minimum 4 min, round to int
    
    # Fallback: Use distance-based estimation with better constants
    subway_dist = calculate_distance(
        origin_station['latitude'], origin_station['longitude'],
        dest_station['latitude'], dest_station['longitude']
    )
    
    if shared_lines:
        # Direct route - use better speed estimate
        # NYC subway average speed: ~28-32 km/h in Manhattan, ~35-40 km/h in outer areas
        # Use 30 km/h as a conservative average for direct routes
        subway_time = int((subway_dist / 1000) / 30 * 60)
    else:
        # Transfer route - slower and add transfer time
        # Average speed for routes with transfers: ~25 km/h
        subway_time = int((subway_dist / 1000) / 25 * 60) + 4  # +4 min for transfer
    
    return max(5, subway_time)  # Minimum 5 min

def find_route_options(origin_lat, origin_lon, dest_lat, dest_lon, avoid_lines=None):
    """Find route options with three different strategies."""
    # Optimized: Use 5x5 for speed (25 combinations is sufficient coverage and much faster)
    # Reduced from 10x10 to improve performance
    origin_stations = find_nearest_stations(origin_lat, origin_lon, limit=5, max_distance=2000)
    dest_stations = find_nearest_stations(dest_lat, dest_lon, limit=5, max_distance=2000)
    
    if not origin_stations or not dest_stations:
        return None, None, None
    
    route_options = []
    
    # Pre-calculate frequency scores for origin stations only (for safest option)
    origin_frequency_scores = {}
    for origin_station, origin_dist in origin_stations:
        walk_to_time = int((origin_dist / 1000) / 4.5 * 60)  # Slower walking: 4.5 km/h
        avg_gap, max_gap, frequency_score = analyze_train_frequency(
            origin_station['id'], origin_station['lines'], walk_to_time
        )
        origin_frequency_scores[origin_station['id']] = {
            'frequency_score': frequency_score,
            'avg_gap': avg_gap,
            'max_gap': max_gap
        }
    
    # Try all combinations (10x10 = 100 combinations for better coverage)
    for origin_station, origin_dist in origin_stations:
        for dest_station, dest_dist in dest_stations:
            total_walking = origin_dist + dest_dist
            
            # Prefer routes where stations share lines (easier transfer)
            origin_lines = set(origin_station['lines'].split(',')) if origin_station['lines'] else set()
            dest_lines = set(dest_station['lines'].split(',')) if dest_station['lines'] else set()
            shared_lines = origin_lines.intersection(dest_lines)
            
            # If we need to avoid specific lines (for contingency routes), filter them out
            if avoid_lines:
                shared_lines = shared_lines - set(avoid_lines)
                # If no shared lines left after filtering, and we're not allowing transfers yet, skip
                # (For now we only support direct routes in this simple logic, transfers are handled by "not shared_lines")
            
            # Calculate walking time estimates (improved: 4.5 km/h average walking speed)
            walk_to_time = int((origin_dist / 1000) / 4.5 * 60)  # 4.5 km/h walking speed (more realistic)
            walk_from_time = int((dest_dist / 1000) / 4.5 * 60)
            
            # Apply learned corrections if available
            corrections = get_learned_corrections()
            if 'walking_correction' in corrections:
                correction = corrections['walking_correction']['factor']
                # Apply correction (negative means we overestimate, positive means we underestimate)
                walk_to_time = max(1, int(walk_to_time - correction))
                walk_from_time = max(1, int(walk_from_time - correction))
            
            # Use improved subway time estimation
            if shared_lines:
                # Direct route
                subway_time = estimate_subway_time_improved(origin_station, dest_station, shared_lines)
                segments = [{
                    "from_id": origin_station['id'],
                    "to_id": dest_station['id'],
                    "line": list(shared_lines)[0], # Pick one
                    "stations": [] # We could fill this if needed
                }]
            else:
                # Transfer route - use Graph!
                from app.utils.graph import find_path
                segments = find_path(origin_station['id'], dest_station['id'])
                
                if segments:
                    # Calculate time based on segments
                    # 2 min per station + 4 min per transfer
                    subway_time = 0
                    for i, seg in enumerate(segments):
                        # Station time
                        count = len(seg['stations']) - 1 # Edges
                        subway_time += count * 2
                        
                        # Transfer time (if not last segment)
                        if i < len(segments) - 1:
                            subway_time += 4
                    
                    # Add initial boarding
                    subway_time += 2
                else:
                    # Fallback if graph fails
                    subway_dist = calculate_distance(
                        origin_station['latitude'], origin_station['longitude'],
                        dest_station['latitude'], dest_station['longitude']
                    )
                    subway_time = int((subway_dist / 1000) / 25 * 60) + 4
                    segments = []
            
            # Apply learned corrections if available
            corrections = get_learned_corrections()
            if 'subway_correction' in corrections:
                correction = corrections['subway_correction']['factor']
                # Apply correction
                subway_time = max(4, int(subway_time - correction))
            
            # Use cached frequency score
            freq_data = origin_frequency_scores.get(origin_station['id'], {
                'frequency_score': 100,
                'avg_gap': 15,
                'max_gap': 30
            })
            
            # Calculate route score (lower is better)
            # Prioritize: direct routes, shorter total time, less walking
            route_score = walk_to_time + subway_time + walk_from_time
            if not shared_lines:
                route_score += 8  # Reduced penalty for transfer routes (was 10)
            
            # Bonus for very short walking distances (prefer nearby stations)
            if total_walking < 500:  # Less than 500m total walking
                route_score -= 2  # Small bonus
            elif total_walking > 1500:  # More than 1.5km total walking
                route_score += 3  # Penalty for long walks
            
            route_options.append({
                "origin_station": origin_station,
                "dest_station": dest_station,
                "origin_walk_dist": origin_dist,
                "dest_walk_dist": dest_dist,
                "walk_to_time": walk_to_time,
                "walk_from_time": walk_from_time,
                "subway_time": subway_time,
                "total_walking": total_walking,
                "total_time": walk_to_time + subway_time + walk_from_time,
                "route_score": route_score,  # For better sorting
                "shared_lines": list(shared_lines),
                "direct_line": len(shared_lines) > 0,
                "segments": segments, # NEW: Explicit segments
                "frequency_score": freq_data['frequency_score'],
                "avg_gap": freq_data['avg_gap'],
                "max_gap": freq_data['max_gap']
            })
    
    if not route_options:
        return None, None, None
    
    # Remove duplicates (same station pairs)
    seen = set()
    unique_options = []
    for option in route_options:
        key = (option['origin_station']['id'], option['dest_station']['id'])
        if key not in seen:
            seen.add(key)
            unique_options.append(option)
    
    # Sort options by different criteria
    # Improved sorting: prioritize route_score which already includes bonuses/penalties
    sorted_by_frequency = sorted(unique_options, key=lambda x: (
        x['frequency_score'],   # Lower is better
        x['route_score'],       # Then by overall score
        x['total_walking']      # Then by walking
    ))
    
    sorted_by_walking = sorted(unique_options, key=lambda x: (
        x['total_walking'],     # Less walking
        x['route_score'],       # Then by overall score
        x['total_time']         # Then by time
    ))
    
    sorted_by_time = sorted(unique_options, key=lambda x: (
        x['route_score'],       # Best overall score (includes direct route bonus)
        x['total_time'],        # Then by time
        x['total_walking']      # Then by walking
    ))
    
    # Find three best options:
    # 1. Safest: Best frequency score (lowest = most frequent trains)
    safest = sorted_by_frequency[0] if sorted_by_frequency else None
    
    # 2. Least walking: Minimum total walking distance
    least_walking = sorted_by_walking[0] if sorted_by_walking else None
    
    # 3. Fastest: Minimum total time (using route_score for better accuracy)
    fastest = sorted_by_time[0] if sorted_by_time else None
    
    # If all three are the same route, try to find alternatives
    if safest == least_walking == fastest and len(unique_options) > 1:
        # Try to get different routes
        if len(sorted_by_frequency) > 1:
            safest = sorted_by_frequency[1]
        if len(sorted_by_walking) > 1:
            least_walking = sorted_by_walking[1]
        if len(sorted_by_time) > 1:
            fastest = sorted_by_time[1]
        
        # If still same, use top 3 by different criteria
        if safest == least_walking == fastest:
            if len(sorted_by_frequency) > 2:
                safest = sorted_by_frequency[2]
            if len(sorted_by_walking) > 2:
                least_walking = sorted_by_walking[2]
            if len(sorted_by_time) > 2:
                fastest = sorted_by_time[2]
    
    return safest, least_walking, fastest

def save_route_insight(origin_coords, dest_coords, our_routes, google_routes):
    """
    Save insights from Google Maps comparison to database for future learning.
    Also learns general patterns that can be applied to other routes.
    """
    from app.db import get_db
    import hashlib
    import json
    
    if not our_routes or not google_routes:
        return
    
    db = get_db()
    
    # Create a hash for this route pair (rounded to ~100m precision)
    origin_lat = round(origin_coords['latitude'], 3)
    origin_lon = round(origin_coords['longitude'], 3)
    dest_lat = round(dest_coords['latitude'], 3)
    dest_lon = round(dest_coords['longitude'], 3)
    
    route_hash = hashlib.md5(f"{origin_lat},{origin_lon},{dest_lat},{dest_lon}".encode()).hexdigest()
    
    # Compare all three of our routes
    our_fastest = min(our_routes, key=lambda r: r['total_time']['total'])
    our_least_walking = min(our_routes, key=lambda r: r['total_time']['walking'])
    our_safest = min(our_routes, key=lambda r: r.get('train_frequency', {}).get('avg_gap', 999))
    
    google_fastest = min(google_routes, key=lambda r: r['total_duration'])
    
    # Calculate insights
    time_diff = our_fastest['total_time']['total'] - google_fastest['total_duration']
    
    our_origin = our_fastest.get('origin_station', {}).get('name', '')
    our_dest = our_fastest.get('destination_station', {}).get('name', '')
    google_origin = google_fastest['transit_steps'][0]['from'] if google_fastest.get('transit_steps') else ''
    google_dest = google_fastest['transit_steps'][-1]['to'] if google_fastest.get('transit_steps') else ''
    
    insights = {
        'time_diff': time_diff,
        'our_origin': our_origin,
        'our_dest': our_dest,
        'google_origin': google_origin,
        'google_dest': google_dest,
        'our_fastest_time': our_fastest['total_time']['total'],
        'google_time': google_fastest['total_duration'],
        'our_walking': our_fastest['total_time']['walking'],
        'google_walking': sum(w['duration'] for w in google_fastest.get('walking_steps', []))
    }
    
    insights_json = json.dumps(insights)
    
    # Calculate confidence based on sample count and consistency
    # More samples = higher confidence
    existing = db.execute(
        'SELECT id, sample_count, time_diff FROM route_insights WHERE route_hash = ?',
        (route_hash,)
    ).fetchone()
    
    if existing:
        # Update existing insight (average the times)
        new_count = existing['sample_count'] + 1
        old_time_diff = existing['time_diff']
        
        # Calculate confidence: higher if samples are consistent and numerous
        consistency = 1.0 - min(abs(time_diff - old_time_diff) / max(abs(old_time_diff), 1), 1.0)
        confidence = min(0.5 + (new_count * 0.1) + (consistency * 0.3), 1.0)  # Max 1.0
        
        # Weighted average
        db.execute('''
            UPDATE route_insights 
            SET google_time = (google_time * ? + ?) / ?,
                our_time = (our_time * ? + ?) / ?,
                time_diff = (time_diff * ? + ?) / ?,
                insights = ?,
                sample_count = ?,
                confidence = ?,
                last_updated = CURRENT_TIMESTAMP
            WHERE route_hash = ?
        ''', (
            existing['sample_count'], google_fastest['total_duration'], new_count,
            existing['sample_count'], our_fastest['total_time']['total'], new_count,
            existing['sample_count'], time_diff, new_count,
            insights_json, new_count, confidence, route_hash
        ))
    else:
        # Insert new insight (low initial confidence)
        confidence = 0.2
        db.execute('''
            INSERT INTO route_insights 
            (origin_lat, origin_lon, dest_lat, dest_lon, route_hash, google_time, our_time, time_diff, 
             google_stations, our_stations, insights, sample_count, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ''', (
            origin_lat, origin_lon, dest_lat, dest_lon, route_hash,
            google_fastest['total_duration'], our_fastest['total_time']['total'], time_diff,
            f"{google_origin}→{google_dest}", f"{our_origin}→{our_dest}",
            insights_json, confidence
        ))
    
    # Learn general patterns (not just this specific route)
    # Pattern 1: Walking time correction
    walking_diff = our_fastest['total_time']['walking'] - sum(w['duration'] for w in google_fastest.get('walking_steps', []))
    if abs(walking_diff) > 1:
        # Learn: are we consistently over/under-estimating walking?
        pattern_key = "walking_correction"
        existing_pattern = db.execute(
            'SELECT * FROM route_patterns WHERE pattern_type = ? AND pattern_key = ?',
            ('time_estimation', pattern_key)
        ).fetchone()
        
        if existing_pattern:
            new_count = existing_pattern['sample_count'] + 1
            # Weighted average of correction factor
            new_correction = (existing_pattern['correction_factor'] * existing_pattern['sample_count'] + walking_diff) / new_count
            db.execute('''
                UPDATE route_patterns 
                SET correction_factor = ?, sample_count = ?, last_updated = CURRENT_TIMESTAMP
                WHERE pattern_type = ? AND pattern_key = ?
            ''', (new_correction, new_count, 'time_estimation', pattern_key))
        else:
            db.execute('''
                INSERT INTO route_patterns (pattern_type, pattern_key, correction_factor, sample_count)
                VALUES (?, ?, ?, 1)
            ''', ('time_estimation', pattern_key, walking_diff))
    
    # Pattern 2: Subway time correction
    our_subway = our_fastest['total_time']['subway']
    google_subway = sum(t['duration'] for t in google_fastest.get('transit_steps', []))
    subway_diff = our_subway - google_subway
    if abs(subway_diff) > 2:
        pattern_key = "subway_correction"
        existing_pattern = db.execute(
            'SELECT * FROM route_patterns WHERE pattern_type = ? AND pattern_key = ?',
            ('time_estimation', pattern_key)
        ).fetchone()
        
        if existing_pattern:
            new_count = existing_pattern['sample_count'] + 1
            new_correction = (existing_pattern['correction_factor'] * existing_pattern['sample_count'] + subway_diff) / new_count
            db.execute('''
                UPDATE route_patterns 
                SET correction_factor = ?, sample_count = ?, last_updated = CURRENT_TIMESTAMP
                WHERE pattern_type = ? AND pattern_key = ?
            ''', (new_correction, new_count, 'time_estimation', pattern_key))
        else:
            db.execute('''
                INSERT INTO route_patterns (pattern_type, pattern_key, correction_factor, sample_count)
                VALUES (?, ?, ?, 1)
            ''', ('time_estimation', pattern_key, subway_diff))
    
    db.commit()

def get_route_insight(origin_coords, dest_coords):
    """
    Get learned insights for a route pair.
    Returns insight data if available, None otherwise.
    """
    from app.db import get_db
    import hashlib
    
    db = get_db()
    
    # Create hash for lookup
    origin_lat = round(origin_coords['latitude'], 3)
    origin_lon = round(origin_coords['longitude'], 3)
    dest_lat = round(dest_coords['latitude'], 3)
    dest_lon = round(dest_coords['longitude'], 3)
    
    route_hash = hashlib.md5(f"{origin_lat},{origin_lon},{dest_lat},{dest_lon}".encode()).hexdigest()
    
    insight = db.execute(
        'SELECT * FROM route_insights WHERE route_hash = ?',
        (route_hash,)
    ).fetchone()
    
    if insight:
        import json
        insights = json.loads(insight['insights']) if insight['insights'] else {}
        return {
            'time_diff': insight['time_diff'],
            'google_time': insight['google_time'],
            'our_time': insight['our_time'],
            'sample_count': insight['sample_count'],
            'confidence': insight.get('confidence', 0.0),
            'insights': insights
        }
    return None

def get_learned_corrections():
    """
    Get learned correction factors from general patterns.
    These can be applied to improve our time estimates.
    """
    from app.db import get_db
    
    db = get_db()
    patterns = db.execute(
        'SELECT pattern_key, correction_factor, sample_count FROM route_patterns WHERE pattern_type = ?',
        ('time_estimation',)
    ).fetchall()
    
    corrections = {}
    for pattern in patterns:
        # Only use patterns with enough samples (at least 5)
        if pattern['sample_count'] >= 5:
            corrections[pattern['pattern_key']] = {
                'factor': pattern['correction_factor'],
                'samples': pattern['sample_count']
            }
    
    return corrections

def should_compare_with_google(origin_coords, dest_coords):
    """
    Determine if we should compare with Google Maps.
    Returns True if we should compare, False otherwise.
    
    Strategy:
    - Always compare new routes (no insights)
    - Compare routes with low confidence (< 0.7)
    - Randomly compare 5% of high-confidence routes (to validate)
    - Never compare if confidence > 0.9 and samples > 10
    """
    import random
    
    insight = get_route_insight(origin_coords, dest_coords)
    
    if not insight:
        # New route - always learn
        return True
    
    confidence = insight.get('confidence', 0.0)
    sample_count = insight.get('sample_count', 0)
    
    # High confidence with many samples - rarely compare
    if confidence > 0.9 and sample_count > 10:
        return random.random() < 0.01  # 1% chance
    
    # Medium confidence - compare sometimes
    if confidence > 0.7:
        return random.random() < 0.05  # 5% chance
    
    # Low confidence - compare more often
    if confidence > 0.5:
        return random.random() < 0.2  # 20% chance
    
    # Very low confidence - compare often
    return random.random() < 0.5  # 50% chance

def analyze_route_comparison(our_routes, google_routes, origin_coords, dest_coords):
    """
    Compare our routes with Google Maps routes and provide analysis.
    Now compares all three route options (safest, least walking, fastest).
    Returns a list of analysis strings for debugging.
    """
    if not our_routes or not google_routes:
        return None
    
    analysis = []
    
    # Get all three of our route options
    our_fastest = min(our_routes, key=lambda r: r['total_time']['total'])
    our_least_walking = min(our_routes, key=lambda r: r['total_time']['walking'])
    our_safest = min(our_routes, key=lambda r: r.get('train_frequency', {}).get('avg_gap', 999))
    
    our_fastest_time = our_fastest['total_time']['total']
    
    # Get Google's best route
    google_fastest = min(google_routes, key=lambda r: r['total_duration'])
    google_fastest_time = google_fastest['total_duration']
    
    # Save insights for future learning
    save_route_insight(origin_coords, dest_coords, our_routes, google_routes)
    
    # Compare all three route options
    analysis.append(f"\n{'='*60}")
    analysis.append(f"ROUTE COMPARISON: All Three Options")
    analysis.append(f"{'='*60}")
    
    # 1. Fastest route comparison
    time_diff_fastest = our_fastest_time - google_fastest_time
    if abs(time_diff_fastest) < 2:
        analysis.append(f"✓ FASTEST: Our route ({our_fastest_time:.1f} min) matches Google's ({google_fastest_time:.1f} min) - within 2 min")
    elif time_diff_fastest > 0:
        analysis.append(f"⚠ FASTEST: Our route ({our_fastest_time:.1f} min) is {time_diff_fastest:.1f} min SLOWER than Google's ({google_fastest_time:.1f} min)")
    else:
        analysis.append(f"✓ FASTEST: Our route ({our_fastest_time:.1f} min) is {abs(time_diff_fastest):.1f} min FASTER than Google's ({google_fastest_time:.1f} min)")
    
    # 2. Least walking comparison
    our_walking_time = our_least_walking['total_time']['walking']
    google_walking_time = sum(w['duration'] for w in google_fastest.get('walking_steps', []))
    walking_diff = our_walking_time - google_walking_time
    if abs(walking_diff) < 1:
        analysis.append(f"✓ LEAST WALKING: Our route ({our_walking_time:.1f} min walk) matches Google's ({google_walking_time:.1f} min)")
    elif walking_diff > 0:
        analysis.append(f"⚠ LEAST WALKING: Our route ({our_walking_time:.1f} min walk) is {walking_diff:.1f} min MORE walking than Google's ({google_walking_time:.1f} min)")
    else:
        analysis.append(f"✓ LEAST WALKING: Our route ({our_walking_time:.1f} min walk) is {abs(walking_diff):.1f} min LESS walking than Google's ({google_walking_time:.1f} min)")
    
    # 3. Safest route info
    safest_avg_gap = our_safest.get('train_frequency', {}).get('avg_gap', 0)
    analysis.append(f"✓ SAFEST: Our route has {safest_avg_gap:.1f} min average train gap")
    
    # Compare route details
    analysis.append(f"\nOur Fastest Route:")
    origin_station = our_fastest.get('origin_station', {})
    dest_station = our_fastest.get('destination_station', {})
    
    origin_name = origin_station.get('name', 'Unknown')
    origin_lines = origin_station.get('lines', [])
    if isinstance(origin_lines, str):
        origin_lines = origin_lines.split(',')
    origin_lines_str = ', '.join(origin_lines[:3]) if origin_lines else 'N/A'
    
    dest_name = dest_station.get('name', 'Unknown')
    dest_lines = dest_station.get('lines', [])
    if isinstance(dest_lines, str):
        dest_lines = dest_lines.split(',')
    dest_lines_str = ', '.join(dest_lines[:3]) if dest_lines else 'N/A'
    
    analysis.append(f"  From: {origin_name} ({origin_lines_str})")
    analysis.append(f"  To: {dest_name} ({dest_lines_str})")
    analysis.append(f"  Walk: {our_fastest['total_time']['walking']} min, Subway: {our_fastest['total_time']['subway']} min")
    analysis.append(f"  Direct route: {our_fastest.get('direct_line', False)}")
    if our_fastest.get('trains') and len(our_fastest['trains']) > 0:
        primary_train = our_fastest['trains'][0]
        analysis.append(f"  Next train: {primary_train.get('route_id', '?')} in {primary_train.get('minutes_away', 0)} min")
    
    analysis.append(f"\nGoogle's Fastest Route:")
    if google_fastest['transit_steps']:
        first_transit = google_fastest['transit_steps'][0]
        analysis.append(f"  From: {first_transit['from']}")
        analysis.append(f"  To: {first_transit['to']}")
        analysis.append(f"  Line: {first_transit['line']}")
        total_walk = sum(w['duration'] for w in google_fastest['walking_steps'])
        total_transit = sum(t['duration'] for t in google_fastest['transit_steps'])
        analysis.append(f"  Walk: {total_walk:.1f} min, Subway: {total_transit:.1f} min")
        if len(google_fastest['transit_steps']) > 1:
            analysis.append(f"  Transfers: {len(google_fastest['transit_steps']) - 1}")
    
    # Check if we're using the same stations
    our_origin_name = origin_name.lower()
    our_dest_name = dest_name.lower()
    
    google_origin_match = False
    google_dest_match = False
    
    if google_fastest['transit_steps']:
        google_origin = google_fastest['transit_steps'][0]['from'].lower()
        google_dest = google_fastest['transit_steps'][-1]['to'].lower()
        
        # Fuzzy matching
        if any(word in google_origin for word in our_origin_name.split() if len(word) > 3):
            google_origin_match = True
        if any(word in google_dest for word in our_dest_name.split() if len(word) > 3):
            google_dest_match = True
    
    if google_origin_match and google_dest_match:
        analysis.append(f"\n✓ We're using the same stations as Google")
    else:
        if not google_origin_match:
            analysis.append(f"\n⚠ Different origin stations - Google: {google_fastest['transit_steps'][0]['from'] if google_fastest['transit_steps'] else 'N/A'}, Ours: {origin_name}")
        if not google_dest_match:
            analysis.append(f"⚠ Different destination stations - Google: {google_fastest['transit_steps'][-1]['to'] if google_fastest['transit_steps'] else 'N/A'}, Ours: {dest_name}")
    
    # Check if we're missing a better route
    if time_diff > 3:  # Our route is significantly slower (reduced threshold from 5 to 3)
        analysis.append(f"\n🔍 INVESTIGATION NEEDED:")
        analysis.append(f"  Our route is {time_diff:.1f} min slower than Google's")
        analysis.append(f"  Possible reasons:")
        analysis.append(f"    - We might be missing a better station combination")
        analysis.append(f"    - Our time estimates might be off")
        analysis.append(f"    - We might not be considering transfers properly")
        if not our_fastest.get('direct_line', False) and len(google_fastest.get('transit_steps', [])) == 1:
            analysis.append(f"    - Google found a direct route, we didn't")
        
        # Provide specific improvement suggestions
        our_walk = our_fastest['total_time']['walking']
        google_walk = sum(w['duration'] for w in google_fastest.get('walking_steps', []))
        if our_walk > google_walk + 2:
            analysis.append(f"    - We're walking {our_walk - google_walk:.1f} min more (consider closer stations)")
        
        our_subway = our_fastest['total_time']['subway']
        google_subway = sum(t['duration'] for t in google_fastest.get('transit_steps', []))
        if our_subway > google_subway + 3:
            analysis.append(f"    - Our subway time estimate ({our_subway} min) is {our_subway - google_subway:.1f} min higher than Google's ({google_subway:.1f} min)")
    
    elif time_diff < -2:  # We're faster!
        analysis.append(f"\n✓ EXCELLENT: Our route is {abs(time_diff):.1f} min FASTER than Google's!")
    
    # Check walking distances
    # Try to get walking distance from route data structure
    our_walk_dist = 0
    if 'origin_station' in our_fastest and isinstance(our_fastest['origin_station'], dict):
        our_walk_dist += our_fastest['origin_station'].get('walk_distance', 0)
    if 'destination_station' in our_fastest and isinstance(our_fastest['destination_station'], dict):
        our_walk_dist += our_fastest['destination_station'].get('walk_distance', 0)
    
    # If not found in nested structure, try direct access
    if our_walk_dist == 0:
        our_walk_dist = our_fastest.get('total_walking', 0) / 1000  # Convert from meters to km if needed
    
    google_walk_dist = sum(w.get('distance', 0) for w in google_fastest.get('walking_steps', []))
    
    if our_walk_dist > 0 and google_walk_dist > 0:
        walk_diff = (our_walk_dist - google_walk_dist) * 1000  # Convert to meters
        if abs(walk_diff) > 200:
            if walk_diff > 0:
                analysis.append(f"\n⚠ We're walking {walk_diff:.0f}m MORE than Google ({our_walk_dist*1000:.0f}m vs {google_walk_dist*1000:.0f}m)")
            else:
                analysis.append(f"\n✓ We're walking {abs(walk_diff):.0f}m LESS than Google ({our_walk_dist*1000:.0f}m vs {google_walk_dist*1000:.0f}m)")
    
    return analysis

def get_google_maps_route(origin_lat, origin_lon, dest_lat, dest_lon):
    """Get route suggestions from Google Maps Directions API for comparison."""
    if not GOOGLE_MAPS_API_KEY:
        return None
    
    try:
        url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            "origin": f"{origin_lat},{origin_lon}",
            "destination": f"{dest_lat},{dest_lon}",
            "mode": "transit",
            "transit_mode": "subway",
            "alternatives": "true",  # Get multiple route options
            "key": GOOGLE_MAPS_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'OK' and data.get('routes'):
                routes = []
                for route in data['routes']:
                    # Extract route information
                    legs = route.get('legs', [])
                    if legs:
                        leg = legs[0]
                        total_duration = leg.get('duration', {}).get('value', 0) / 60  # Convert to minutes
                        total_distance = leg.get('distance', {}).get('value', 0) / 1000  # Convert to km
                        
                        # Extract transit steps
                        steps = leg.get('steps', [])
                        transit_steps = []
                        walking_steps = []
                        
                        for step in steps:
                            travel_mode = step.get('travel_mode', '')
                            if travel_mode == 'TRANSIT':
                                transit = step.get('transit_details', {})
                                line = transit.get('line', {})
                                route_short_name = line.get('short_name', '')
                                route_name = line.get('name', '')
                                departure_stop = transit.get('departure_stop', {}).get('name', '')
                                arrival_stop = transit.get('arrival_stop', {}).get('name', '')
                                duration = step.get('duration', {}).get('value', 0) / 60
                                
                                transit_steps.append({
                                    'line': route_short_name or route_name,
                                    'from': departure_stop,
                                    'to': arrival_stop,
                                    'duration': duration
                                })
                            elif travel_mode == 'WALKING':
                                distance = step.get('distance', {}).get('value', 0) / 1000
                                duration = step.get('duration', {}).get('value', 0) / 60
                                walking_steps.append({
                                    'distance': distance,
                                    'duration': duration
                                })
                        
                        routes.append({
                            'total_duration': total_duration,
                            'total_distance': total_distance,
                            'transit_steps': transit_steps,
                            'walking_steps': walking_steps,
                            'summary': route.get('summary', ''),
                            'warnings': route.get('warnings', [])
                        })
                
                return routes
    except Exception as e:
        print(f"Google Maps API error: {e}")
    
    return None

@bp.get("/route")
def route_planner():
    """Display route planner page."""
    return render_template("route.html")

@bp.post("/api/route/plan")
def plan_route():
    """Plan a route from origin to destination."""
    data = request.get_json()
    origin = data.get('origin')
    destination = data.get('destination')
    
    if not origin or not destination:
        return jsonify({"error": "Origin and destination required"}), 400
    
    # Check if coordinates are provided directly (format: "lat,lon")
    origin_coords = None
    dest_coords = None
    
    if ',' in origin and origin.count(',') == 1:
        try:
            parts = origin.split(',')
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            # Reverse geocode to get a proper address
            display_name = reverse_geocode(lat, lon)
            origin_coords = {
                "latitude": lat,
                "longitude": lon,
                "display_name": display_name or f"Location ({lat:.4f}, {lon:.4f})"
            }
        except ValueError:
            pass
    
    if ',' in destination and destination.count(',') == 1:
        try:
            parts = destination.split(',')
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            # Reverse geocode to get a proper address
            display_name = reverse_geocode(lat, lon)
            dest_coords = {
                "latitude": lat,
                "longitude": lon,
                "display_name": display_name or f"Location ({lat:.4f}, {lon:.4f})"
            }
        except ValueError:
            pass
    
    # Geocode addresses if not coordinates
    if not origin_coords:
        origin_coords = geocode_address(origin)
    if not dest_coords:
        dest_coords = geocode_address(destination)
    
    if not origin_coords or not dest_coords:
        return jsonify({"error": "Could not geocode addresses. Please try a different address or use 'Use My Location'."}), 400
    
    # Find three route options: safest, least walking, fastest
    safest_option, least_walking_option, fastest_option = find_route_options(
        origin_coords["latitude"], origin_coords["longitude"],
        dest_coords["latitude"], dest_coords["longitude"]
    )
    
    # Handle case where we might not have all three options
    if not safest_option:
        return jsonify({"error": "Could not find nearby subway stations. Please try different locations."}), 400
    
    # Use safest as fallback if others are missing
    if not least_walking_option:
        least_walking_option = safest_option
    if not fastest_option:
        fastest_option = safest_option
    
    # Get Google Maps routes for comparison (optional)
    # Smart learning: only compare when needed based on confidence
    should_compare = should_compare_with_google(origin_coords, dest_coords)
    
    google_routes = None
    if should_compare and GOOGLE_MAPS_API_KEY:
        try:
            google_routes = get_google_maps_route(
                origin_coords["latitude"], origin_coords["longitude"],
                dest_coords["latitude"], dest_coords["longitude"]
            )
        except Exception as e:
            print(f"Could not fetch Google Maps routes: {e}")
    
    # Apply learned corrections to improve our time estimates
    corrections = get_learned_corrections()
    if corrections:
        print(f"Applying learned corrections: {len(corrections)} patterns")
        # These corrections can be used to adjust time estimates
        # For now, we'll use them in the analysis
    
    # Get real-time train arrivals for each route option
    from app.blueprints.station import _parse_next_arrivals
    from datetime import datetime
    
    routes_with_trains = []
    route_options = [
        (safest_option, "safest", "Safest Bet", "Frequent trains - if you miss one, another is coming soon"),
        (least_walking_option, "least_walking", "Least Walking", "Minimizes total walking distance"),
        (fastest_option, "fastest", "Fastest", "Shortest total travel time")
    ]
    
    # Get alerts to check for route-specific delays
    from app.blueprints.status import _parse_alerts
    all_alerts = _parse_alerts()
    
    # Get alerts to check for route-specific delays
    from app.blueprints.status import _parse_alerts
    all_alerts = _parse_alerts()
    
    # Helper to process a route option and get full data
    def _process_route_data(option, strategy_id, strategy_name, strategy_desc):
        origin_station = option['origin_station']
        dest_station = option['dest_station']
        # Use ceil to be safe - better to overestimate walk time than miss a train
        import math
        walk_to_time = math.ceil(option['walk_to_time'])
        
        # Check for delays on the ACTUAL train line being used in this route
        route_delays = []
        
        # Find nearest entrance locations (not station centers)
        origin_entrance_lat, origin_entrance_lon, origin_entrance_desc = find_nearest_entrance(
            origin_coords["latitude"], origin_coords["longitude"],
            origin_station['id'], origin_station['latitude'], origin_station['longitude']
        )
        if origin_entrance_lat is None or origin_entrance_lon is None:
            origin_entrance_lat, origin_entrance_lon = origin_station['latitude'], origin_station['longitude']
            origin_entrance_desc = "Main Entrance"
        
        dest_entrance_lat, dest_entrance_lon, dest_entrance_desc = find_nearest_entrance(
            dest_coords["latitude"], dest_coords["longitude"],
            option['dest_station']['id'], option['dest_station']['latitude'], option['dest_station']['longitude']
        )
        if dest_entrance_lat is None or dest_entrance_lon is None:
            dest_entrance_lat, dest_entrance_lon = option['dest_station']['latitude'], option['dest_station']['longitude']
            dest_entrance_desc = "Main Entrance"
        
        # Get walking route to/from entrance locations
        walk_to_station = get_walking_route(
            origin_coords["latitude"], origin_coords["longitude"],
            origin_entrance_lat, origin_entrance_lon
        )
        
        walk_from_station = get_walking_route(
            dest_entrance_lat, dest_entrance_lon,
            dest_coords["latitude"], dest_coords["longitude"]
        )
        
        # Calculate effective walking times (using ORS if available, else heuristic)
        # Also apply 3-minute buffer to origin walk for non-fastest routes (apartment exit time)
        origin_walk_time = walk_to_station['duration'] if walk_to_station else walk_to_time
        dest_walk_time = walk_from_station['duration'] if walk_from_station else option['walk_from_time']
        
        if strategy_id != 'fastest':
            origin_walk_time += 3
            
        # Update walk_to_time variable for train filtering logic
        walk_to_time = origin_walk_time
        
        # Get train arrivals for origin station
        arrivals = _parse_next_arrivals(origin_station['lines'])
        
        if not arrivals:
            relevant_trains = []
        else:
            # Priority 1: Trains arriving between walk_time and walk_time + 10 minutes (ideal window)
            # Priority 2: Next train after walk_time (even if > 10 min)
            # Priority 3: Trains arriving soon (if walk_time is long, show what's coming)
            
            ideal_trains = []
            next_trains = []
            
            # Target times:
            # Primary train: walk_time + 2 min (2 min buffer after you arrive)
            # Backup train: walk_time + 5 min or later (5+ min buffer)
            primary_target = walk_to_time + 2
            backup_target = walk_to_time + 5
            
            for arrival in arrivals:
                minutes_away = arrival.get('minutes_away', 0)
                
                # CRITICAL: Only show trains that arrive AFTER you reach the station
                # Primary train should arrive around walk_time + 2 min (2 min buffer)
                # Accept trains arriving between walk_time + 1 and walk_time + 4 (1-4 min after arrival)
                if minutes_away > walk_to_time and minutes_away <= (walk_to_time + 4):
                    ideal_trains.append(arrival)
                # Next available: trains after walk time (for fallback)
                elif minutes_away > walk_to_time:
                    next_trains.append(arrival)
            
            # Build relevant trains list with priority
            relevant_trains = []
            
            # Primary train: Find train arriving closest to walk_time + 2 min
            if ideal_trains:
                # Sort by how close they are to the target (walk_time + 2 min)
                ideal_trains.sort(key=lambda x: abs(x.get('minutes_away', 999) - primary_target))
                relevant_trains.append(ideal_trains[0])
            elif next_trains:
                # Fallback: use next available train after walk time
                next_trains.sort(key=lambda x: x.get('minutes_away', 999))
                relevant_trains.append(next_trains[0])
            
            # If still no trains arriving after walk time, we need to look further ahead
            if not relevant_trains:
                future_trains = [a for a in arrivals if a.get('minutes_away', 0) > walk_to_time]
                if future_trains:
                    future_trains.sort(key=lambda x: x.get('minutes_away', 999))
                    relevant_trains.append(future_trains[0])
            
            # Last resort: if absolutely no trains after walk time
            if not relevant_trains and arrivals:
                all_zero = all(a.get('minutes_away', 0) == 0 for a in arrivals[:10])
                if all_zero and walk_to_time > 0:
                    estimated_arrival = primary_target
                    if arrivals:
                        sample_train = arrivals[0].copy()
                        sample_train['minutes_away'] = estimated_arrival
                        from datetime import datetime, timedelta
                        estimated_time = datetime.now() + timedelta(minutes=estimated_arrival)
                        sample_train['arrival_time'] = estimated_time.strftime("%I:%M %p")
                        sample_train['estimated'] = True
                        relevant_trains.append(sample_train)
                else:
                    arrivals_sorted = sorted(arrivals, key=lambda x: x.get('minutes_away', 999))
                    relevant_trains.append(arrivals_sorted[0])
            
            # Sort by arrival time
            relevant_trains.sort(key=lambda x: x.get('minutes_away', 999))
            
            # For safest option, ALWAYS ensure we have a backup train
            if strategy_id == 'safest' and len(relevant_trains) == 1:
                primary_train = relevant_trains[0]
                primary_minutes = primary_train.get('minutes_away', 0)
                
                backup_trains = []
                for arrival in arrivals:
                    minutes_away = arrival.get('minutes_away', 0)
                    if minutes_away >= backup_target and minutes_away <= (walk_to_time + 20):
                        is_duplicate = False
                        if arrival.get('route_id') == primary_train.get('route_id') and abs(minutes_away - primary_minutes) <= 1:
                            is_duplicate = True
                        if not is_duplicate:
                            backup_trains.append(arrival)
                
                backup_trains.sort(key=lambda x: abs(x.get('minutes_away', 999) - backup_target))
                
                if backup_trains:
                    relevant_trains.append(backup_trains[0])
                else:
                    backup_estimated_arrival = backup_target
                    backup_train = primary_train.copy()
                    backup_train['minutes_away'] = backup_estimated_arrival
                    from datetime import datetime, timedelta
                    backup_time = datetime.now() + timedelta(minutes=backup_estimated_arrival)
                    backup_train['arrival_time'] = backup_time.strftime("%I:%M %p")
                    backup_train['estimated'] = True
                    if len(arrivals) > 0:
                        different_routes = [a for a in arrivals if a.get('route_id') != primary_train.get('route_id')]
                        if different_routes:
                            backup_train['route_id'] = different_routes[0].get('route_id', primary_train.get('route_id'))
                    relevant_trains.append(backup_train)
            
            # Sort by arrival time
            relevant_trains.sort(key=lambda x: x.get('minutes_away', 999))
            
            # Limit to primary train + backup
            max_trains = 2 if strategy_id == 'safest' else 1
            relevant_trains = relevant_trains[:max_trains]
        
        # Calculate actual subway time using MTA real-time data
        actual_subway_time = None
        if relevant_trains and len(relevant_trains) > 0:
            primary_train = relevant_trains[0]
            primary_train_route = primary_train.get('route_id', '')
            primary_train_minutes = primary_train.get('minutes_away', 0)
            primary_train_trip_id = primary_train.get('trip_id')
            
            actual_subway_time = get_actual_subway_time(
                origin_station, 
                option['dest_station'], 
                primary_train_route,
                primary_train_minutes,
                trip_id=primary_train_trip_id
            )
            
            if actual_subway_time:
                time_diff = abs(actual_subway_time - option['subway_time'])
                if time_diff > 1:
                    option['subway_time'] = actual_subway_time
        
        # Check for delays and intermediate stations
        intermediate_stations = []
        train_line = None
        if relevant_trains and len(relevant_trains) > 0:
            primary_train_line = relevant_trains[0].get('route_id', '')
            if primary_train_line:
                train_line = primary_train_line[0] if primary_train_line else ''
                intermediate_stations = find_intermediate_stations(
                    origin_station, option['dest_station'], train_line
                )
                
                for alert in all_alerts:
                    affected_lines_str = alert.get('affected_lines', '')
                    if affected_lines_str:
                        affected_lines = [line.strip() for line in affected_lines_str.split(',')]
                        if train_line in affected_lines:
                            if alert.get('severity') in ['warning', 'error']:
                                route_delays.append({
                                    'line': train_line,
                                    'status': 'Delays' if alert.get('severity') == 'warning' else 'Service Suspended',
                                    'alert': {
                                        'header': alert.get('header', ''),
                                        'description': alert.get('description', '')
                                    }
                                })
                                break
        
        # Calculate Reliability Score
        reliability_score = 100
        reliability_reasons = []
        
        if route_delays:
            reliability_score -= 30
            reliability_reasons.append("Service alerts reported")
            
        avg_gap = option.get('avg_gap', 0)
        if avg_gap > 15:
            reliability_score -= 15
            reliability_reasons.append("Infrequent trains")
        elif avg_gap > 10:
            reliability_score -= 5
            
        if not option.get('direct_line', False):
            reliability_score -= 10
            reliability_reasons.append("Requires transfer")
            
        if not relevant_trains or any(t.get('estimated', False) for t in relevant_trains):
            reliability_score -= 10
            reliability_reasons.append("Using estimated schedule")
            
        reliability_score = max(0, min(100, reliability_score))
        
        return {
            "strategy": strategy_id,
            "strategy_name": strategy_name,
            "strategy_desc": strategy_desc,
            "reliability_score": reliability_score,
            "reliability_reasons": reliability_reasons,
            "origin_station": {
                "id": origin_station['id'],
                "name": origin_station['name'],
                "coordinates": [origin_station['latitude'], origin_station['longitude']],
                "entrance_coordinates": [origin_entrance_lat, origin_entrance_lon],
                "entrance_description": origin_entrance_desc,
                "lines": origin_station['lines'].split(',') if origin_station['lines'] else [],
                "walk_distance": round(walk_to_station.get("distance", option['origin_walk_dist']) / 1000, 2) if walk_to_station else round(option['origin_walk_dist'] / 1000, 2),
                "walk_time": origin_walk_time,
                "walk_route": walk_to_station.get("route") if walk_to_station else None
            },
            "destination_station": {
                "id": option['dest_station']['id'],
                "name": option['dest_station']['name'],
                "coordinates": [option['dest_station']['latitude'], option['dest_station']['longitude']],
                "entrance_coordinates": [dest_entrance_lat, dest_entrance_lon],
                "entrance_description": dest_entrance_desc,
                "lines": option['dest_station']['lines'].split(',') if option['dest_station']['lines'] else [],
                "walk_distance": round(walk_from_station.get("distance", option['dest_walk_dist']) / 1000, 2) if walk_from_station else round(option['dest_walk_dist'] / 1000, 2),
                "walk_time": dest_walk_time,
                "walk_route": walk_from_station.get("route") if walk_from_station else None
            },
            "trains": relevant_trains,
            "total_time": {
                "walking": origin_walk_time + dest_walk_time,
                "subway": actual_subway_time if actual_subway_time else option['subway_time'],
                "total": (origin_walk_time + dest_walk_time) + (actual_subway_time if actual_subway_time else option['subway_time'])
            },
            "direct_line": option['direct_line'],
            "shared_lines": option['shared_lines'],
            "train_frequency": {
                "avg_gap": round(option.get('avg_gap', 0), 1),
                "max_gap": round(option.get('max_gap', 0), 1)
            },
            "delays": route_delays,
            "intermediate_stations": [s['name'] for s in intermediate_stations] if intermediate_stations else [],
            "subway_path": _build_subway_path(origin_station, option['dest_station'], intermediate_stations)
        }

    # Process standard options
    for option, strategy_id, strategy_name, strategy_desc in route_options:
        routes_with_trains.append(_process_route_data(option, strategy_id, strategy_name, strategy_desc))
    
    # SMART CONTINGENCY CHECK
    # Check if the "Safest" route (first one) has low reliability
    if routes_with_trains:
        safest_route = routes_with_trains[0]
        if safest_route['reliability_score'] < 70:
            print(f"DEBUG: Safest route reliability is low ({safest_route['reliability_score']}%) - looking for contingency")
            
            # Identify lines to avoid
            avoid_lines = set(safest_route['shared_lines'])
            
            # Find a contingency route
            contingency_option = find_contingency_route(
                origin_coords["latitude"], origin_coords["longitude"],
                dest_coords["latitude"], dest_coords["longitude"],
                avoid_lines=list(avoid_lines)
            )
            
            if contingency_option:
                print("DEBUG: Found contingency route!")
                # Process the contingency route
                contingency_data = _process_route_data(
                    contingency_option, 
                    "contingency", 
                    "Smart Contingency", 
                    f"Avoids {', '.join(avoid_lines)} due to reliability issues"
                )
                
                # Add it to the list (as the second option, right after safest)
                routes_with_trains.insert(1, contingency_data)


    
    response_data = {
        "origin": {
            "address": origin,
            "coordinates": [origin_coords["latitude"], origin_coords["longitude"]],
            "display_name": origin_coords.get("display_name", origin)
        },
        "destination": {
            "address": destination,
            "coordinates": [dest_coords["latitude"], dest_coords["longitude"]],
            "display_name": dest_coords.get("display_name", destination)
        },
        "routes": routes_with_trains
    }
    
    # Analyze and compare with Google Maps routes (for debugging/validation)
    if google_routes:
        analysis = analyze_route_comparison(routes_with_trains, google_routes, origin_coords, dest_coords)
        if analysis:
            print("\n" + "="*60)
            print("ROUTE COMPARISON ANALYSIS")
            print("="*60)
            for line in analysis:
                print(line)
            print("="*60 + "\n")
    
    return jsonify(response_data)

