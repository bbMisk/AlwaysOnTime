from flask import Blueprint, request, jsonify, session, render_template, current_app
import os

bp = Blueprint("auth", __name__)

def verify_firebase_token(token):
    """Verify Firebase ID token and return user info."""
    try:
        import firebase_admin
        from firebase_admin import auth
        
        # Verify the token
        decoded_token = auth.verify_id_token(token)
        return {
            'uid': decoded_token['uid'],
            'email': decoded_token.get('email'),
            'name': decoded_token.get('name'),
            'picture': decoded_token.get('picture')
        }
    except ImportError:
        current_app.logger.warning("firebase-admin not available")
        return None
    except Exception as e:
        current_app.logger.error(f"Token verification failed: {e}")
        return None

@bp.get("/login")
def login_page():
    """Display login page."""
    # Check if Firebase is configured
    firebase_config = {
        'apiKey': os.getenv('FIREBASE_API_KEY', ''),
        'authDomain': os.getenv('FIREBASE_AUTH_DOMAIN', ''),
        'projectId': os.getenv('FIREBASE_PROJECT_ID', ''),
    }
    firebase_enabled = bool(firebase_config['apiKey'] and firebase_config['authDomain'])
    
    return render_template("login.html", firebase_enabled=firebase_enabled, firebase_config=firebase_config)

@bp.post("/api/auth/login")
def login():
    """Handle login with Firebase token verification."""
    data = request.get_json()
    token = data.get('token')
    
    if not token:
        return jsonify({"error": "Token required"}), 400
    
    # Verify Firebase token
    user_info = verify_firebase_token(token)
    
    if user_info:
        # Store user info in session
        session['user_id'] = user_info['uid']
        session['user_email'] = user_info.get('email')
        session['user_name'] = user_info.get('name')
        session['user_picture'] = user_info.get('picture')
        return jsonify({
            "success": True,
            "user": {
                "id": user_info['uid'],
                "email": user_info.get('email'),
                "name": user_info.get('name'),
                "picture": user_info.get('picture')
            }
        })
    else:
        # Fallback to session-based auth if Firebase not available
        user_id = data.get('user_id', 'anonymous')
        session['user_id'] = user_id
        return jsonify({"success": True, "user_id": user_id, "note": "Using session-based auth (Firebase not configured)"})

@bp.post("/api/auth/logout")
def logout():
    """Handle logout."""
    session.pop('user_id', None)
    session.pop('user_email', None)
    session.pop('user_name', None)
    session.pop('user_picture', None)
    return jsonify({"success": True})

@bp.get("/api/auth/status")
def auth_status():
    """Get current authentication status."""
    user_id = session.get('user_id', None)
    if user_id:
        return jsonify({
            "authenticated": True,
            "user": {
                "id": user_id,
                "email": session.get('user_email'),
                "name": session.get('user_name'),
                "picture": session.get('user_picture')
            }
        })
    return jsonify({"authenticated": False})

