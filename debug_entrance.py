
import sqlite3
import math
import os
import requests

# Connect to database
db_path = os.path.join('instance', 'alwaysontime.db')
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def get_osrm_route(start_lat, start_lon, end_lat, end_lon):
    try:
        url = f"http://router.project-osrm.org/route/v1/walking/{start_lon},{start_lat};{end_lon},{end_lat}"
        params = {"overview": "false"}
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if "routes" in data and len(data["routes"]) > 0:
                return data["routes"][0]["distance"]
    except Exception as e:
        print(f"OSRM error: {e}")
    return None

# User location
user_lat = 40.6932
user_lon = -73.9835

# Get DeKalb Av station
cursor.execute("SELECT * FROM stations WHERE name = 'DeKalb Av' AND lines LIKE '%B%'")
station = cursor.fetchone()

# Get entrances
cursor.execute("SELECT * FROM station_entrances WHERE station_id = ?", (station['id'],))
entrances = cursor.fetchall()

print(f"User: {user_lat}, {user_lon}")
print(f"Station Center: {station['latitude']}, {station['longitude']}")

best_entrance = None
min_dist = float('inf')

# Find closest entrance by straight line
for ent in entrances:
    dist = calculate_distance(user_lat, user_lon, ent['latitude'], ent['longitude'])
    if dist < min_dist:
        min_dist = dist
        best_entrance = ent

print(f"\nClosest Entrance (Straight Line): {best_entrance['description']}")
print(f"Coords: {best_entrance['latitude']}, {best_entrance['longitude']}")
print(f"Straight Line Dist: {min_dist:.1f}m")

# Check OSRM distance
osrm_dist = get_osrm_route(user_lat, user_lon, best_entrance['latitude'], best_entrance['longitude'])
print(f"OSRM Route Dist: {osrm_dist:.1f}m" if osrm_dist else "OSRM Failed")

if osrm_dist and osrm_dist > min_dist * 2:
    print("⚠ DETOUR DETECTED! Route is > 2x straight line distance.")
    
    # Check other entrances
    print("\nChecking other entrances...")
    for ent in entrances:
        if ent['id'] == best_entrance['id']:
            continue
            
        straight_dist = calculate_distance(user_lat, user_lon, ent['latitude'], ent['longitude'])
        route_dist = get_osrm_route(user_lat, user_lon, ent['latitude'], ent['longitude'])
        
        print(f"- {ent['description']}: Straight={straight_dist:.1f}m, Route={route_dist:.1f}m")
        
        if route_dist and route_dist < osrm_dist:
            print("  -> BETTER OPTION!")

conn.close()
