# app/api/batch.py
# RESTful API endpoints for batch processing

from flask import Blueprint, request, jsonify, g
from typing import Dict, Any, List
import logging
import threading

from app.core.auth import require_api_token, auth_settings, quota_exceeded

from app.services.storage import (
    add_batch, get_batch_status, get_recent_batches,
    get_queue_stats, init_db, create_discovering_batch,
    update_discovery_progress, finalize_discovery
)
from app.services.hybrid_discover import discover_forms_smart

logger = logging.getLogger(__name__)

# Create Blueprint
batch_bp = Blueprint('batch', __name__, url_prefix='/api/batch')

# Initialize database on import
# Database initialized in create_app

# =============================================================================
# Background Discovery
# =============================================================================

def discover_async(batch_id: str, seed_url: str, max_forms: int):
    """
    Background thread to discover forms and update progress
    NOTE: Currently unused - synchronous discovery is used instead to avoid SQLite locking
    """
    try:
        logger.info(f"Starting async hybrid discovery for batch {batch_id}: {seed_url}")
        update_discovery_progress(batch_id, 0, 'in_progress')

        # Use hybrid discovery (4-tier cascade)
        pdf_urls = discover_forms_smart(
            url=seed_url,
            max_forms=max_forms,
            strategy="auto"
        )

        if not pdf_urls:
            update_discovery_progress(batch_id, 0, 'completed')
            finalize_discovery(batch_id, [])
            logger.info(f"No PDFs found for batch {batch_id}")
            return

        # Update with discovered count
        update_discovery_progress(batch_id, len(pdf_urls), 'completed')

        # Finalize batch with discovered URLs
        finalize_discovery(batch_id, pdf_urls)

        logger.info(f"Discovery complete for batch {batch_id}: {len(pdf_urls)} forms")

    except Exception as e:
        logger.error(f"Async discovery error for batch {batch_id}: {str(e)}")
        update_discovery_progress(batch_id, 0, 'failed')

# =============================================================================
# API Endpoints
# =============================================================================

@batch_bp.route('/create', methods=['POST'])
@require_api_token
def create_batch():
    """
    Create a new batch of jobs. Requires ``Authorization: Bearer <token>``.

    The server caps URLs per request (FORMGENOME_MAX_BATCH_URLS), discovery
    size (FORMGENOME_MAX_DISCOVERY_FORMS), and jobs queued per identity per
    day (FORMGENOME_DAILY_QUEUED_JOBS).

    Request body (Option 1 - Direct PDF URLs):
    {
        "urls": ["https://example.com/form1.pdf", "https://example.com/form2.pdf"],
        "name": "Optional batch name",
        "config": {
            "priority": 0,
            "max_retries": 3
        }
    }

    Request body (Option 2 - Website Discovery):
    {
        "url": "https://example.com",
        "max_forms": 5000,
        "name": "Optional batch name"
    }

    Response:
    {
        "success": true,
        "batch_id": "batch_1234567890_abc123",
        "total_jobs": 100,
        "message": "Batch created successfully"
    }
    """
    try:
        settings = auth_settings()
        identity = g.api_identity

        # Parse request
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({
                "success": False,
                "error": "Invalid JSON"
            }), 400

        # Check if this is a discovery request (single URL)
        if 'url' in data and 'urls' not in data:
            # Website discovery mode - SYNCHRONOUS to avoid SQLite locking issues
            seed_url = data.get('url', '').strip()
            if not seed_url:
                return jsonify({
                    "success": False,
                    "error": "URL cannot be empty"
                }), 400

            max_forms = data.get('max_forms', settings.max_discovery_forms)
            if isinstance(max_forms, bool) or not isinstance(max_forms, int) or max_forms < 1:
                return jsonify({
                    "success": False,
                    "error": "max_forms must be a positive integer"
                }), 400
            max_forms = min(max_forms, settings.max_discovery_forms)
            batch_name = data.get('name', f"Scan of {seed_url}")

            # Reserve the worst case up front and refund what discovery does not
            # queue. The discovery run itself always costs one unit.
            if not settings.job_quota.reserve(identity, max_forms):
                return quota_exceeded("Daily batch job quota exceeded", settings.job_quota.remaining(identity))
            unused = max_forms - 1
            try:
                logger.info(f"Starting hybrid discovery for {seed_url} (max {max_forms} forms)")

                # Use hybrid discovery (4-tier cascade: CSE → Sitemap → Crawler → LLM)
                try:
                    pdf_urls = discover_forms_smart(
                        url=seed_url,
                        max_forms=max_forms,
                        strategy="auto"  # Auto-selects best strategy based on domain
                    )
                except Exception as e:
                    logger.error(f"Hybrid discovery failed for {seed_url}: {str(e)}")
                    return jsonify({
                        "success": False,
                        "error": f"Discovery failed: {str(e)}"
                    }), 400

                if not pdf_urls:
                    return jsonify({
                        "success": False,
                        "error": "No PDF forms found on this website"
                    }), 404

                pdf_urls = list(pdf_urls)[:max_forms]
                # add_batch commits jobs one at a time, so a failure part-way
                # through still leaves earlier jobs queued; keep those charged.
                unused = max_forms - len(pdf_urls)

                # Create batch with discovered URLs (no threading, no async)
                batch_id = add_batch(
                    urls=pdf_urls,
                    batch_name=batch_name,
                    config={},
                    discovery_status='completed'
                )
            finally:
                settings.job_quota.release(identity, unused)

            logger.info(f"Synchronous discovery complete for batch {batch_id}: {len(pdf_urls)} forms")

            return jsonify({
                "success": True,
                "batch_id": batch_id,
                "total_jobs": len(pdf_urls),
                "status": "created",
                "message": f"Batch created with {len(pdf_urls)} forms"
            }), 201

        # Original behavior: Direct PDF URLs
        urls = data.get('urls', [])
        if not urls:
            return jsonify({
                "success": False,
                "error": "No URLs provided"
            }), 400

        if not isinstance(urls, list):
            return jsonify({
                "success": False,
                "error": "URLs must be a list"
            }), 400

        if len(urls) > settings.max_batch_urls:
            return jsonify({
                "success": False,
                "error": f"Too many URLs: at most {settings.max_batch_urls} per batch"
            }), 400

        # Filter valid URLs
        valid_urls = []
        for url in urls:
            if isinstance(url, str) and url.strip().lower().endswith('.pdf'):
                valid_urls.append(url.strip())

        if not valid_urls:
            return jsonify({
                "success": False,
                "error": "No valid PDF URLs provided"
            }), 400

        # Optional parameters
        batch_name = data.get('name')
        config = data.get('config', {})

        if not settings.job_quota.reserve(identity, len(valid_urls)):
            return quota_exceeded("Daily batch job quota exceeded", settings.job_quota.remaining(identity))

        # Create batch
        batch_id = add_batch(
            urls=valid_urls,
            batch_name=batch_name,
            config=config
        )

        logger.info(f"Created batch {batch_id} with {len(valid_urls)} jobs")

        return jsonify({
            "success": True,
            "batch_id": batch_id,
            "total_jobs": len(valid_urls),
            "message": f"Batch created successfully with {len(valid_urls)} jobs"
        }), 201

    except Exception as e:
        logger.error(f"Batch creation failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@batch_bp.route('/status/<batch_id>', methods=['GET'])
def get_status(batch_id: str):
    """
    Get status of a batch.

    Response:
    {
        "success": true,
        "batch": {
            "batch_id": "batch_1234567890_abc123",
            "name": "My Batch",
            "total_jobs": 100,
            "completed_jobs": 75,
            "failed_jobs": 5,
            "status": "running",
            "progress": 80.0,
            "created_at": 1234567890,
            "completed_at": null,
            "job_breakdown": {
                "pending": 20,
                "processing": 5,
                "completed": 75,
                "failed": 5
            }
        }
    }
    """
    try:
        batch = get_batch_status(batch_id)

        if not batch:
            return jsonify({
                "success": False,
                "error": "Batch not found"
            }), 404

        return jsonify({
            "success": True,
            "batch": batch
        })

    except Exception as e:
        logger.error(f"Failed to get batch status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@batch_bp.route('/list', methods=['GET'])
def list_batches():
    """
    List recent batches.

    Query parameters:
    - limit: Number of batches to return (default 10, max 100)

    Response:
    {
        "success": true,
        "batches": [
            {
                "batch_id": "batch_1234567890_abc123",
                "name": "My Batch",
                "total_jobs": 100,
                "completed_jobs": 100,
                "failed_jobs": 0,
                "status": "completed",
                "created_at": 1234567890,
                "completed_at": 1234567990
            },
            ...
        ],
        "count": 10
    }
    """
    try:
        # Parse limit parameter
        limit = request.args.get('limit', default=10, type=int)
        limit = max(1, min(limit, 100))  # Clamp between 1 and 100

        # Get batches
        batches = get_recent_batches(limit=limit)

        return jsonify({
            "success": True,
            "batches": batches,
            "count": len(batches)
        })

    except Exception as e:
        logger.error(f"Failed to list batches: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@batch_bp.route('/stats', methods=['GET'])
def get_stats():
    """
    Get overall queue statistics.

    Response:
    {
        "success": true,
        "stats": {
            "pending": 50,
            "processing": 5,
            "completed": 1000,
            "failed": 10,
            "total": 1065
        }
    }
    """
    try:
        stats = get_queue_stats()

        return jsonify({
            "success": True,
            "stats": stats
        })

    except Exception as e:
        logger.error(f"Failed to get stats: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@batch_bp.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint.

    Response:
    {
        "success": true,
        "status": "healthy",
        "database": "connected"
    }
    """
    try:
        # Try to get stats to verify database connection
        get_queue_stats()

        return jsonify({
            "success": True,
            "status": "healthy",
            "database": "connected"
        })

    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({
            "success": False,
            "status": "unhealthy",
            "error": str(e)
        }), 500


# =============================================================================
# Error Handlers
# =============================================================================

@batch_bp.errorhandler(404)
def not_found(e):
    """Handle 404 errors"""
    return jsonify({
        "success": False,
        "error": "Endpoint not found"
    }), 404


@batch_bp.errorhandler(405)
def method_not_allowed(e):
    """Handle 405 errors"""
    return jsonify({
        "success": False,
        "error": "Method not allowed"
    }), 405


@batch_bp.errorhandler(500)
def internal_error(e):
    """Handle 500 errors"""
    logger.error(f"Internal error: {str(e)}")
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


# =============================================================================
# Blueprint Registration Helper
# =============================================================================

def register_batch_api(app):
    """
    Register batch API blueprint with Flask app.

    Usage:
        from flask import Flask
        from app.api.batch import register_batch_api

        app = Flask(__name__)
        register_batch_api(app)
    """
    app.register_blueprint(batch_bp)
    logger.info("Batch API registered")
