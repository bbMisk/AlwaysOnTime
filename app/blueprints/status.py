import os
import requests
from flask import Blueprint, jsonify, render_template
from google.transit import gtfs_realtime_pb2

bp = Blueprint("status", __name__)

# MTA GTFS-realtime feed URLs (public endpoints, no API key required)
# These are the actual public endpoints from api-endpoint.mta.info
MTA_FEED_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds"
MTA_FEEDS = {
    "nyct%2Fgtfs": "1,2,3,4,5,6,S",
    "nyct%2Fgtfs-ace": "A,C,E,H",
    "nyct%2Fgtfs-bdfm": "B,D,F,M",
    "nyct%2Fgtfs-g": "G",
    "nyct%2Fgtfs-jz": "J,Z",
    "nyct%2Fgtfs-l": "L",
    "nyct%2Fgtfs-nqrw": "N,Q,R,W",
    "nyct%2Fgtfs-si": "SIR"  # Staten Island Railway
}

def _mta_session(require_key=False):
    """Create requests session with MTA API key (optional)."""
    api_key = os.getenv("MTA_API_KEY", "")
    s = requests.Session()
    if api_key:
        s.headers.update({"x-api-key": api_key})
    elif require_key:
        return None
    return s

# Cache for GTFS feeds (feed_path -> (feed, timestamp))
_feed_cache = {}
_feed_cache_timeout = 30  # Cache for 30 seconds

def _fetch_gtfs_feed(feed_path):
    """Fetch and parse a GTFS-realtime feed from public MTA endpoints."""
    import time
    
    # Check cache
    if feed_path in _feed_cache:
        feed, timestamp = _feed_cache[feed_path]
        if time.time() - timestamp < _feed_cache_timeout:
            return feed
            
    # These are public endpoints, no API key needed
    url = f"{MTA_FEED_BASE}/{feed_path}"
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)
        
        # Update cache
        _feed_cache[feed_path] = (feed, time.time())
        return feed
    except Exception as e:
        # Log error for debugging but don't fail completely
        print(f"Error fetching feed {feed_path}: {e}")
        return None

def _parse_service_status():
    """Parse service status from GTFS-realtime feeds."""
    # Try to fetch real data first (may work without key)
    
    status_data = {
        "subway": {"status": "GOOD SERVICE", "lines": []},
        "bus": {"status": "GOOD SERVICE", "lines": []},
        "lirr": {"status": "GOOD SERVICE", "lines": []},
        "metro_north": {"status": "GOOD SERVICE", "lines": []}
    }
    
    # Try to fetch from first feed to check connectivity
    first_feed_path = list(MTA_FEEDS.keys())[0]
    feed = _fetch_gtfs_feed(first_feed_path)
    if feed is None:
        # If we can't fetch, return stub
        return status_data
    
    # Parse alerts from feeds to determine service status
    has_delays = False
    has_planned_work = False
    
    for feed_path in MTA_FEEDS.keys():
        feed = _fetch_gtfs_feed(feed_path)
        if feed:
            for entity in feed.entity:
                if entity.HasField('alert'):
                    alert = entity.alert
                    
                    # Extract alert text
                    header_text = ""
                    description_text = ""
                    if alert.header_text and alert.header_text.translation:
                        header_text = alert.header_text.translation[0].text if len(alert.header_text.translation) > 0 else ""
                    if alert.description_text and alert.description_text.translation:
                        description_text = alert.description_text.translation[0].text if len(alert.description_text.translation) > 0 else ""
                    
                    # Check for service disruptions
                    header_lower = header_text.lower()
                    desc_lower = description_text.lower()
                    
                    if 'delay' in header_lower or 'delayed' in header_lower or 'delay' in desc_lower:
                        has_delays = True
                    if 'planned' in header_lower or 'work' in header_lower or 'service change' in header_lower:
                        has_planned_work = True
    
    # Set overall status
    if has_delays:
        status_data["subway"]["status"] = "DELAYS"
    elif has_planned_work:
        status_data["subway"]["status"] = "PLANNED WORK"
    else:
        status_data["subway"]["status"] = "GOOD SERVICE"
    
    return status_data

def _parse_alerts():
    """Parse service alerts from GTFS-realtime feeds."""
    # Try to fetch real data first (may work without key)
    
    alerts = []
    seen_ids = set()
    
    for feed_path, lines in MTA_FEEDS.items():
        feed = _fetch_gtfs_feed(feed_path)
        if feed:
            for entity in feed.entity:
                if entity.HasField('alert') and entity.id not in seen_ids:
                    alert = entity.alert
                    seen_ids.add(entity.id)
                    
                    header_text = ""
                    description_text = ""
                    affected_lines = []
                    
                    if alert.header_text and alert.header_text.translation:
                        header_text = alert.header_text.translation[0].text if len(alert.header_text.translation) > 0 else ""
                    if alert.description_text and alert.description_text.translation:
                        description_text = alert.description_text.translation[0].text if len(alert.description_text.translation) > 0 else ""
                    
                    for informed_entity in alert.informed_entity:
                        if informed_entity.HasField('route_id'):
                            affected_lines.append(informed_entity.route_id)
                    
                    # Determine severity
                    severity = "info"
                    header_lower = header_text.lower()
                    if "delay" in header_lower or "delayed" in header_lower:
                        severity = "warning"
                    elif "suspended" in header_lower or "closed" in header_lower:
                        severity = "error"
                    
                    alerts.append({
                        "id": entity.id,
                        "header": header_text or "Service Alert",
                        "description": description_text or "",
                        "affected_lines": ",".join(affected_lines) if affected_lines else lines,
                        "severity": severity
                    })
    
    return alerts[:10]  # Limit to 10 most recent

@bp.get("/status")
def service_status():
    """Return service status as HTML fragment for HTMX."""
    status_data = _parse_service_status()
    return render_template("partials/status_panel.html", status=status_data)

@bp.get("/status/json")
def service_status_json():
    """Return service status as JSON (for API consumers)."""
    status_data = _parse_service_status()
    return jsonify(status_data)

@bp.get("/alerts")
def alerts():
    """Return service alerts as HTML fragment for HTMX."""
    alerts_data = _parse_alerts()
    return render_template("partials/alerts_panel.html", alerts=alerts_data)

@bp.get("/alerts/json")
def alerts_json():
    """Return service alerts as JSON (for API consumers)."""
    alerts_data = _parse_alerts()
    return jsonify(alerts_data)

@bp.get("/accessibility")
def accessibility():
    """Return accessibility information (elevator/escalator status)."""
    from app.models import Station
    
    # Get stations with accessibility info
    stations = Station.get_all()
    accessible_stations = [s for s in stations if s['accessible'] == 1]
    
    # For now, return stub data since MTA doesn't provide real-time elevator status easily
    accessibility_data = {
        "total_stations": len(stations),
        "accessible_stations": len(accessible_stations),
        "stations": [
            {
                "name": s['name'],
                "lines": s['lines'],
                "accessible": bool(s['accessible'])
            }
            for s in accessible_stations[:20]  # Limit to 20
        ]
    }
    
    return render_template("partials/accessibility_panel.html", data=accessibility_data)

@bp.get("/accessibility/json")
def accessibility_json():
    """Return accessibility information as JSON."""
    from app.models import Station
    from app.utils.accessibility import fetch_elevator_status
    
    stations = Station.get_all()
    accessible_stations = [s for s in stations if s['accessible'] == 1]
    
    # Fetch real-time outages
    outages = fetch_elevator_status()
    
    # Map outages to stations
    station_status = []
    for s in accessible_stations:
        status = "Operational"
        details = []
        
        # Check if station has outages
        # Try exact name match first
        station_name = s['name']
        if station_name in outages:
            details = outages[station_name]
            status = "Outage" if details else "Operational"
        else:
            # Try fuzzy matching or common variations if needed
            # For now, simple name match
            pass
            
        station_status.append({
            "name": s['name'],
            "lines": s['lines'],
            "accessible": bool(s['accessible']),
            "status": status,
            "outages": details
        })
    
    accessibility_data = {
        "total_stations": len(stations),
        "accessible_stations": len(accessible_stations),
        "stations": station_status
    }
    
    return jsonify(accessibility_data)

@bp.get("/stations")
def stations():
    """Return station data as JSON for map markers."""
    from app.models import Station
    
    stations = Station.get_all()
    
    # Deduplicate stations by name and coordinates (keep first occurrence)
    seen_stations = {}
    unique_stations = []
    for station in stations:
        # Create unique key from name and coordinates (rounded to 4 decimal places)
        key = (station['name'], round(station['latitude'], 4), round(station['longitude'], 4))
        if key not in seen_stations:
            seen_stations[key] = station
            unique_stations.append(station)
    
    stations_data = [
        {
            "id": s['id'],
            "name": s['name'],
            "latitude": s['latitude'],
            "longitude": s['longitude'],
            "lines": s['lines'].split(',') if s['lines'] else [],
            "accessible": bool(s['accessible'])
        }
        for s in unique_stations
    ]
    
    return jsonify({"stations": stations_data})

def _parse_vehicle_positions():
    """Parse vehicle positions from GTFS-realtime feeds."""
    vehicles = []
    
    for feed_path, lines in MTA_FEEDS.items():
        feed = _fetch_gtfs_feed(feed_path)
        if feed:
            for entity in feed.entity:
                # Check for vehicle position entity
                if entity.HasField('vehicle'):
                    vehicle = entity.vehicle
                    if vehicle.HasField('position'):
                        pos = vehicle.position
                        route_id = None
                        if vehicle.HasField('trip') and vehicle.trip.HasField('route_id'):
                            route_id = vehicle.trip.route_id
                        
                        vehicle_data = {
                            "id": entity.id,
                            "latitude": pos.latitude,
                            "longitude": pos.longitude,
                            "bearing": pos.bearing if pos.HasField('bearing') else None,
                            "speed": pos.speed if pos.HasField('speed') else None,
                            "route_id": route_id,
                            "current_stop_sequence": vehicle.current_stop_sequence if vehicle.HasField('current_stop_sequence') else None,
                            "timestamp": vehicle.timestamp if vehicle.HasField('timestamp') else None
                        }
                        vehicles.append(vehicle_data)
                
                # Also check trip updates which may contain vehicle positions
                elif entity.HasField('trip_update'):
                    trip_update = entity.trip_update
                    if trip_update.HasField('vehicle') and trip_update.vehicle.HasField('position'):
                        vehicle = trip_update.vehicle
                        pos = vehicle.position
                        route_id = None
                        if trip_update.HasField('trip') and trip_update.trip.HasField('route_id'):
                            route_id = trip_update.trip.route_id
                        
                        vehicle_data = {
                            "id": f"{entity.id}_trip",
                            "latitude": pos.latitude,
                            "longitude": pos.longitude,
                            "bearing": pos.bearing if pos.HasField('bearing') else None,
                            "speed": pos.speed if pos.HasField('speed') else None,
                            "route_id": route_id,
                            "current_stop_sequence": None,
                            "timestamp": None
                        }
                        vehicles.append(vehicle_data)
    
    return vehicles

@bp.get("/vehicles")
def vehicles():
    """Return live vehicle positions as JSON for map."""
    vehicles_data = _parse_vehicle_positions()
    return jsonify({"vehicles": vehicles_data})
