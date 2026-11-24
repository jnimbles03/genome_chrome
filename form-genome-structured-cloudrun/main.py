#!/usr/bin/env python3
# app.py
# Flask application entry point

import os
import logging
from flask import Flask, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import APIs
from app.api.batch import register_batch_api
from app.api.analyze import register_analyze_api

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# =============================================================================
# Flask Application Factory
# =============================================================================

def create_app():
    """Create and configure Flask application"""

    app = Flask(__name__)

    # Configuration
    app.config['DEBUG'] = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Enable CORS for API access
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    from app.core.auth import init_auth
    init_auth(app)

    # Register APIs
    # Register APIs
    register_batch_api(app)
    # Initialize database
    from app.services.storage import init_db
    try:
        init_db()
    except Exception as e:
        logger.warning(f"Database initialization failed (skipping): {e}")
    register_analyze_api(app)
    from app.api.dashboard import register_dashboard_api
    register_dashboard_api(app)

    # Serve main page (index.html) at root
    @app.route('/')
    def index():
        """Serve the Form Genome Analyzer main page"""
        ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
        return send_from_directory(ui_dir, 'index.html')

    @app.route('/health')
    def health():
        return {"status": "healthy"}

    @app.route('/version')
    def version():
        """Extension version check endpoint"""
        return {
            "version": "1.2.0",
            "min_version": "1.0.0",
            "message": "Update available! Includes Admin Dashboard with Bulk Delete/Re-analyze.",
            "download_url": "https://github.com/jnimbles03/genome_chrome/archive/refs/heads/main.zip"
        }

    @app.route('/status')
    def status():
        """API status endpoint"""
        return {
            "service": "Form Genome Batch Processing API",
            "status": "running",
            "endpoints": {
                "upload_analyze": "POST /api/analyze/upload",
                "create_batch": "POST /api/batch/create",
                "get_status": "GET /api/batch/status/<batch_id>",
                "list_batches": "GET /api/batch/list",
                "queue_stats": "GET /api/batch/stats",
                "batch_health": "GET /api/batch/health",
                "analyze_health": "GET /api/analyze/health"
            }
        }

    # Serve UI
    @app.route('/ui')
    def serve_ui():
        """Serve the drag-and-drop file analysis UI (alias for root)"""
        ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
        return send_from_directory(ui_dir, 'index.html')

    @app.route('/genetics')
    def serve_genetics():
        """Serve the genetics dashboard"""
        ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
        return send_from_directory(ui_dir, 'genetics.html')

    @app.route('/infographic')
    def serve_infographic():
        """Serve the infographic page"""
        ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
        return send_from_directory(ui_dir, 'infographic.html')

    @app.route('/ui/<path:filename>')
    def serve_ui_files(filename):
        """Serve static UI files"""
        ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
        return send_from_directory(ui_dir, filename)

    logger.info("Flask application created")
    return app


# =============================================================================
# Application Entry Point
# =============================================================================

if __name__ == '__main__':
    # Create application
    app = create_app()

    # Get configuration
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', '8080'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'

    logger.info("=" * 60)
    logger.info("Form Genome Batch Processing API")
    logger.info("=" * 60)
    logger.info(f"Host: {host}")
    logger.info(f"Port: {port}")
    logger.info(f"Debug: {debug}")
    logger.info("=" * 60)

    # Run application
    try:
        # Start Batch Processor
        from app.services.batch_processor import BatchProcessor
        processor = BatchProcessor()
        processor.start()
        
        # Attach to app for potential access (hacky but works for simple flask)
        app.batch_processor = processor

        app.run(
            host=host,
            port=port,
            debug=debug,
            use_reloader=False # Disable reloader to prevent double workers
        )
    finally:
        # Graceful shutdown
        logger.info("Shutting down batch processor...")
        if 'processor' in locals():
            processor.stop()
