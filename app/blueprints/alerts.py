from flask import Blueprint, jsonify
from app.blueprints.favorites import get_user_id
from app.blueprints.status import _parse_alerts
from app.db import get_db

bp = Blueprint("alerts", __name__)

@bp.get("/api/favorites/alerts")
def favorite_alerts():
    """Get alerts for user's favorite stations/routes."""
    from flask import request, render_template
    
    user_id = get_user_id()
    db = get_db()
    
    # Get user's favorites
    favorites = db.execute(
        'SELECT station_id, route_id FROM favorites WHERE user_id = ?',
        (user_id,)
    ).fetchall()
    
    if not favorites:
        if request.args.get('format') == 'json':
            return jsonify({"alerts": []})
        return render_template("partials/favorites_alerts_panel.html", alerts=[])
    
    # Get all alerts
    all_alerts = _parse_alerts()
    
    # Filter alerts that affect user's favorites
    favorite_routes = {f['route_id'] for f in favorites if f['route_id']}
    
    relevant_alerts = []
    for alert in all_alerts:
        # Check if alert affects any favorite route
        affected_lines = alert.get('affected_lines', '').split(',')
        if any(route.strip() in favorite_routes for route in affected_lines if route.strip()):
            relevant_alerts.append(alert)
    
    if request.args.get('format') == 'json':
        return jsonify({"alerts": relevant_alerts})
    
    return render_template("partials/favorites_alerts_panel.html", alerts=relevant_alerts)

