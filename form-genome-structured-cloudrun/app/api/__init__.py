# app/api/__init__.py
# API module exports

from app.api.batch import batch_bp, register_batch_api
from app.api.analyze import analyze_bp, register_analyze_api
from app.api.dashboard import dashboard_bp

def register_dashboard_api(app):
    app.register_blueprint(dashboard_bp)

def register_all_apis(app):
    register_batch_api(app)
    register_analyze_api(app)
    register_dashboard_api(app)

__all__ = ['batch_bp', 'analyze_bp', 'dashboard_bp', 'register_all_apis']
