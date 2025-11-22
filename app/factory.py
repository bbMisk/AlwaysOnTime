import os
from flask import Flask
from flask_cors import CORS

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    CORS(app)
    
    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)
    
    # Initialize Firebase Admin SDK (if credentials are provided)
    firebase_creds_path = os.getenv('FIREBASE_CREDENTIALS_PATH')
    if firebase_creds_path and os.path.exists(firebase_creds_path):
        try:
            import firebase_admin
            from firebase_admin import credentials
            
            # Check if Firebase is already initialized
            try:
                firebase_admin.get_app()
            except ValueError:
                # Not initialized, so initialize it
                cred = credentials.Certificate(firebase_creds_path)
                firebase_admin.initialize_app(cred)
                app.logger.info("Firebase Admin SDK initialized")
        except ImportError:
            app.logger.warning("firebase-admin not installed, Firebase auth will be disabled")
        except Exception as e:
            app.logger.warning(f"Firebase initialization failed: {e}")
    else:
        app.logger.info("Firebase credentials not found, using session-based auth")
    
    # Initialize database
    from .db import init_db, close_db, seed_stations, seed_station_entrances
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
        seed_stations()
        seed_station_entrances()

    # Blueprints
    from .blueprints.status import bp as status_bp
    from .blueprints.station import bp as station_bp
    from .blueprints.favorites import bp as favorites_bp
    from .blueprints.alerts import bp as alerts_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.route import bp as route_bp
    app.register_blueprint(status_bp, url_prefix="/api")
    app.register_blueprint(station_bp)
    app.register_blueprint(favorites_bp)
    app.register_blueprint(alerts_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(route_bp)
    
    # Add i18n context processor
    @app.context_processor
    def inject_i18n():
        from .i18n import get_locale, translate, get_translations
        locale = get_locale()
        return {
            'locale': locale,
            't': translate,
            'translations': get_translations(locale)
        }
    
    # Session configuration (for favorites before Firebase)
    app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

    @app.get("/health")
    def health():
        return {"status": "ok"}, 200

    @app.get("/")
    def index():
        # Route home page to route planner
        from flask import redirect
        return redirect("/route")

    return app
