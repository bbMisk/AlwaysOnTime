"""Database models for AlwaysOnTime app."""
from .db import get_db

class Station:
    """Station model."""
    
    @staticmethod
    def get_all():
        """Get all stations."""
        db = get_db()
        return db.execute('SELECT * FROM stations ORDER BY name').fetchall()
    
    @staticmethod
    def get_by_id(station_id):
        """Get station by ID."""
        db = get_db()
        return db.execute('SELECT * FROM stations WHERE id = ?', (station_id,)).fetchone()
    
    @staticmethod
    def create(name, latitude, longitude, lines=None, accessible=0):
        """Create a new station."""
        db = get_db()
        cursor = db.execute(
            'INSERT INTO stations (name, latitude, longitude, lines, accessible) VALUES (?, ?, ?, ?, ?)',
            (name, latitude, longitude, lines, accessible)
        )
        db.commit()
        return cursor.lastrowid

class AlertCache:
    """Alert cache model."""
    
    @staticmethod
    def get_active():
        """Get active alerts."""
        db = get_db()
        return db.execute(
            'SELECT * FROM alerts_cache WHERE expires_at > datetime("now") OR expires_at IS NULL ORDER BY created_at DESC'
        ).fetchall()
    
    @staticmethod
    def create(alert_id, header_text, description_text, affected_lines, severity, expires_at=None):
        """Create or update an alert."""
        db = get_db()
        db.execute(
            '''INSERT OR REPLACE INTO alerts_cache 
               (alert_id, header_text, description_text, affected_lines, severity, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (alert_id, header_text, description_text, affected_lines, severity, expires_at)
        )
        db.commit()

class StationEntrance:
    """Station entrance model."""
    
    @staticmethod
    def get_by_station(station_id):
        """Get all entrances for a station."""
        db = get_db()
        return db.execute(
            'SELECT * FROM station_entrances WHERE station_id = ? ORDER BY id',
            (station_id,)
        ).fetchall()
    
    @staticmethod
    def find_nearest(user_lat, user_lon, station_id):
        """Find the nearest entrance to a user's location for a given station."""
        import math
        db = get_db()
        
        entrances = db.execute(
            'SELECT * FROM station_entrances WHERE station_id = ?',
            (station_id,)
        ).fetchall()
        
        if not entrances:
            return None
        
        # Find the closest entrance using Haversine formula
        nearest = None
        min_distance = float('inf')
        R = 6371000  # Earth radius in meters
        
        for entrance in entrances:
            lat1_rad = math.radians(user_lat)
            lat2_rad = math.radians(entrance['latitude'])
            delta_lat = lat2_rad - lat1_rad
            delta_lon = math.radians(entrance['longitude'] - user_lon)
            
            a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            distance = R * c
            
            if distance < min_distance:
                min_distance = distance
                nearest = entrance
        
        return nearest
    
    @staticmethod
    def create(station_id, latitude, longitude, description=None, accessible=0):
        """Create a new entrance."""
        db = get_db()
        cursor = db.execute(
            'INSERT INTO station_entrances (station_id, latitude, longitude, description, accessible) VALUES (?, ?, ?, ?, ?)',
            (station_id, latitude, longitude, description, accessible)
        )
        db.commit()
        return cursor.lastrowid


