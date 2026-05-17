"""
MiroShark Backend - Flask application factory
"""

import os
import warnings

# Suppress multiprocessing resource_tracker warnings (from third-party libraries like transformers)
# Must be set before all other imports
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request, send_from_directory
from flask_cors import CORS
from flask_compress import Compress

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask application factory function"""
    # Resolve the built frontend static directory (backend/static/).
    # Use the path only if the directory already exists so Flask/Werkzeug
    # does not raise an error when the frontend hasn't been built yet.
    _static_folder = os.path.join(os.path.dirname(__file__), '..', 'static')
    _static_folder = _static_folder if os.path.isdir(_static_folder) else None
    app = Flask(__name__, static_folder=_static_folder, static_url_path='')
    app.config.from_object(config_class)
    
    # Set JSON encoding: ensure non-ASCII characters are displayed directly (instead of \uXXXX format)
    # Flask >= 2.3 uses app.json.ensure_ascii, older versions use JSON_AS_ASCII config
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # Set up logging
    logger = setup_logger('miroshark')
    
    # Only print startup info in the reloader subprocess (avoid printing twice in debug mode)
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroShark Backend starting...")
        logger.info("=" * 50)
    
    # Enable CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Enable gzip/brotli response compression
    Compress(app)

    # --- Initialize Neo4jStorage singleton (DI via app.extensions) ---
    from .storage import Neo4jStorage
    try:
        neo4j_storage = Neo4jStorage()
        app.extensions['neo4j_storage'] = neo4j_storage
        if should_log_startup:
            logger.info("Neo4jStorage initialized (connected to %s)", Config.NEO4J_URI)
    except Exception as e:
        logger.error("Neo4jStorage initialization failed: %s", e)
        # Store None so endpoints can return 503 gracefully
        app.extensions['neo4j_storage'] = None

    # Register simulation process cleanup function (ensure all simulation processes are terminated when server shuts down)
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("Simulation process cleanup function registered")
    
    # Request logging middleware
    @app.before_request
    def log_request():
        logger = get_logger('miroshark.request')
        logger.debug(f"Request: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"Request body: {request.get_json(silent=True)}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('miroshark.request')
        logger.debug(f"Response: {response.status_code}")
        return response
    
    # Register blueprints
    from .api import graph_bp, simulation_bp, report_bp, templates_bp, settings_bp, observability_bp, mcp_bp, docs_bp, feed_bp, share_bp, watch_bp, sitemap_bp, notifications_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    app.register_blueprint(templates_bp, url_prefix='/api/templates')
    app.register_blueprint(settings_bp, url_prefix='/api/settings')
    app.register_blueprint(observability_bp, url_prefix='/api/observability')
    app.register_blueprint(mcp_bp, url_prefix='/api/mcp')
    # docs_bp serves Swagger UI + the OpenAPI spec at /api/docs,
    # /api/openapi.yaml, /api/openapi.json (no extra sub-prefix — the spec
    # URL is the developer-facing surface so we keep it short).
    app.register_blueprint(docs_bp, url_prefix='/api')
    # feed_bp serves the public-gallery syndication feeds at
    # /api/feed.atom + /api/feed.rss — short URLs at the /api root so
    # feed auto-discovery scripts and aggregators find them without
    # digging through the /api/simulation namespace.
    app.register_blueprint(feed_bp, url_prefix='/api')
    # share_bp serves the public OG-tag landing page at /share/<sim_id>
    # (no prefix — keeps the social share URL short).
    app.register_blueprint(share_bp)
    # watch_bp serves the live spectator-watch page at /watch/<sim_id>
    # — same no-prefix policy so the URL stays a clean broadcast link.
    app.register_blueprint(watch_bp)
    # sitemap_bp serves /sitemap.xml + /robots.txt at the root —
    # crawlers expect both URLs at the deployment root, not under
    # /api/, so the blueprint is mounted with no prefix to match the
    # protocol convention.
    app.register_blueprint(sitemap_bp)
    # notifications_bp serves /api/config/notifications — kept on the
    # /api root (no extra sub-prefix) to mirror the sitemap config
    # endpoint that pairs with it on the SPA side.
    app.register_blueprint(notifications_bp)
    
    # Health check
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroShark Backend'}

    # Serve built frontend assets (JS, CSS, images, etc.)
    # Flask's static_folder is already set to backend/static, so files like
    # /assets/index-abc123.js are served automatically by Flask's built-in
    # static file handler.  We only need an explicit catch-all for SPA routing
    # so that deep links (e.g. /graph/123) return index.html instead of 404.
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_spa(path):
        static_dir = app.static_folder
        # If the static folder doesn't exist the frontend hasn't been built yet.
        if not static_dir or not os.path.isdir(static_dir):
            return {'error': 'Frontend not available. The static folder does not exist — run: npm run build'}, 503
        # If the path maps to a real file in the static folder, serve it.
        if path and os.path.exists(os.path.join(static_dir, path)):
            return send_from_directory(static_dir, path)
        # Otherwise fall back to index.html for SPA client-side routing.
        index = os.path.join(static_dir, 'index.html')
        if os.path.exists(index):
            return send_from_directory(static_dir, 'index.html')
        # No built frontend present — return a helpful message.
        return {'error': 'Frontend not built. Run: npm run build'}, 404

    if should_log_startup:
        logger.info("MiroShark Backend startup complete")
    
    return app

