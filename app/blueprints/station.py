from flask import Blueprint, render_template, jsonify, abort
from app.models import Station
from app.blueprints.status import _fetch_gtfs_feed, MTA_FEEDS
from google.transit import gtfs_realtime_pb2
from datetime import datetime, timedelta

bp = Blueprint("station", __name__)

def _parse_next_arrivals(station_lines):
    """Parse next arrivals for a station from GTFS-realtime trip updates."""
    arrivals = []
    seen_arrivals = set()  # Track unique arrivals to avoid duplicates
    
    # Map station lines to feed paths
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
    
    # Get unique feed paths for this station's lines
    feed_paths = set()
    station_line_set = set()
    if station_lines:
        for line in station_lines.split(','):
            line = line.strip()
            station_line_set.add(line)
            if line in line_to_feed:
                feed_paths.add(line_to_feed[line])
    
    # Fetch and parse trip updates
    for feed_path in feed_paths:
        feed = _fetch_gtfs_feed(feed_path)
        if feed:
            for entity in feed.entity:
                if entity.HasField('trip_update'):
                    trip_update = entity.trip_update
                    
                    # Get route ID
                    route_id = None
                    if trip_update.HasField('trip') and trip_update.trip.HasField('route_id'):
                        route_id = trip_update.trip.route_id
                    
                    # Only process if route matches station lines
                    if route_id and route_id[0] not in station_line_set:
                        continue
                    
                    # stop_time_update is a repeated field
                    if len(trip_update.stop_time_update) > 0:
                        for stop_update in trip_update.stop_time_update:
                            if stop_update.HasField('arrival') and stop_update.arrival.HasField('time'):
                                arrival_time = stop_update.arrival.time
                                
                                # Calculate minutes until arrival
                                now = datetime.now().timestamp()
                                minutes_away = int((arrival_time - now) / 60)
                                
                                # Skip trains that have already passed (more than 2 minutes ago)
                                # This filters out trains that are at other stations
                                if minutes_away < -2:
                                    continue
                                
                                # Only show future trains (0 minutes means arriving now/very soon)
                                if minutes_away < 0:
                                    minutes_away = 0
                                
                                # Create unique key to avoid duplicates
                                arrival_key = (route_id, arrival_time)
                                if arrival_key in seen_arrivals:
                                    continue
                                seen_arrivals.add(arrival_key)
                                
                                if minutes_away <= 60:  # Only show arrivals in next hour
                                    direction = None
                                    if trip_update.HasField('trip') and trip_update.trip.HasField('direction_id'):
                                        direction = trip_update.trip.direction_id
                                    
                                    # Get trip_id for matching same train at different stations
                                    trip_id = None
                                    if trip_update.HasField('trip') and trip_update.trip.HasField('trip_id'):
                                        trip_id = trip_update.trip.trip_id
                                    
                                    # Get stop_id for this stop
                                    stop_id = None
                                    if stop_update.HasField('stop_id'):
                                        stop_id = stop_update.stop_id
                                    
                                    arrivals.append({
                                        "route_id": route_id or "?",
                                        "minutes_away": minutes_away,
                                        "arrival_time": datetime.fromtimestamp(arrival_time).strftime("%I:%M %p"),
                                        "direction": direction,
                                        "trip_id": trip_id,
                                        "stop_id": stop_id,
                                        "arrival_timestamp": arrival_time  # Keep raw timestamp for calculations
                                    })
    
    # Sort by arrival time and return more results (we'll filter by station later)
    # Increased limit to get more future trains
    arrivals.sort(key=lambda x: (x['minutes_away'], x['route_id']))
    return arrivals[:30]  # Get more arrivals to find ones that match our time window

@bp.get("/station/<int:station_id>")
def station_detail(station_id):
    """Display station detail page with next arrivals and elevator status."""
    station = Station.get_by_id(station_id)
    if not station:
        abort(404)
    
    # Get next arrivals
    arrivals = _parse_next_arrivals(station['lines'])
    
    return render_template("station.html", station=station, arrivals=arrivals)

@bp.get("/api/station/<int:station_id>/arrivals")
def station_arrivals(station_id):
    """Return next arrivals for a station as HTML fragment or JSON."""
    station = Station.get_by_id(station_id)
    if not station:
        abort(404)
    
    arrivals = _parse_next_arrivals(station['lines'])
    
    # Check if request wants JSON
    from flask import request
    if request.args.get('format') == 'json':
        return jsonify({"arrivals": arrivals})
    
    # Return HTML fragment for HTMX
    from flask import render_template
    return render_template("partials/arrivals_panel.html", arrivals=arrivals)

