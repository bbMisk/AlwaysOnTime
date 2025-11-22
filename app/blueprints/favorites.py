from flask import Blueprint, request, jsonify, session, render_template
from app.models import Station
from app.db import get_db

bp = Blueprint("favorites", __name__)

def get_user_id():
    """Get current user ID from session (Firebase or session-based)."""
    return session.get('user_id', 'anonymous')

@bp.get("/favorites")
def favorites_page():
    """Display favorites management page."""
    return render_template("favorites.html")

@bp.get("/api/favorites")
def get_favorites():
    """Get user's favorite stations and routes."""
    user_id = get_user_id()
    db = get_db()
    
    favorites = db.execute(
        'SELECT f.*, s.name as station_name, s.lines, s.latitude, s.longitude '
        'FROM favorites f '
        'LEFT JOIN stations s ON f.station_id = s.id '
        'WHERE f.user_id = ? '
        'ORDER BY f.created_at DESC',
        (user_id,)
    ).fetchall()
    
    favorites_data = [
        {
            "id": f['id'],
            "station_id": f['station_id'],
            "station_name": f['station_name'],
            "route_id": f['route_id'],
            "lines": f['lines'],
            "latitude": f['latitude'],
            "longitude": f['longitude']
        }
        for f in favorites
    ]
    
    return jsonify({"favorites": favorites_data})

@bp.post("/api/favorites")
def add_favorite():
    """Add a station or route to favorites."""
    user_id = get_user_id()
    data = request.get_json()
    
    station_id = data.get('station_id')
    route_id = data.get('route_id')
    
    if not station_id and not route_id:
        return jsonify({"error": "Must provide station_id or route_id"}), 400
    
    db = get_db()
    try:
        db.execute(
            'INSERT INTO favorites (user_id, station_id, route_id) VALUES (?, ?, ?)',
            (user_id, station_id, route_id)
        )
        db.commit()
        return jsonify({"success": True}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@bp.delete("/api/favorites/<int:favorite_id>")
def remove_favorite(favorite_id):
    """Remove a favorite."""
    user_id = get_user_id()
    db = get_db()
    
    result = db.execute(
        'DELETE FROM favorites WHERE id = ? AND user_id = ?',
        (favorite_id, user_id)
    )
    db.commit()
    
    if result.rowcount > 0:
        return jsonify({"success": True}), 200
    else:
        return jsonify({"error": "Favorite not found"}), 404

