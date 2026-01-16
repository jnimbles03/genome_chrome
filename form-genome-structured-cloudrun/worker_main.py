#!/usr/bin/env python3
"""
Worker service with HTTP health endpoint for Cloud Run
"""
import os
import sys
import threading
import logging
from flask import Flask, jsonify

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create Flask app for health checks
app = Flask(__name__)

# Global processor instance
processor = None

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "form-genome-worker"
    })

@app.route('/', methods=['GET'])
def root():
    """Root endpoint"""
    stats = processor.get_stats() if processor else {}
    return jsonify({
        "service": "form-genome-worker",
        "status": "running",
        "stats": stats
    })

def run_batch_processor():
    """Run the batch processor in a background thread"""
    global processor

    from app.services.batch_processor import BatchProcessor, BatchConfig
    from app.services.storage import init_db

    logger.info("=" * 60)
    logger.info("Form Genome Batch Processor")
    logger.info("=" * 60)

    # Load configuration
    config = BatchConfig.from_env()

    logger.info(f"Configuration:")
    logger.info(f"  Workers: {config.num_workers}")
    logger.info(f"  Rate limit: {config.requests_per_minute} req/min")
    logger.info(f"  PDF timeout: {config.pdf_timeout}s")
    logger.info(f"  Max retries: {config.max_retries}")

    # Initialize database
    init_db()

    # Create and start processor
    processor = BatchProcessor(config)
    processor.start()

    # Wait for completion or interrupt
    try:
        processor.wait()
    except KeyboardInterrupt:
        logger.info("Shutting down batch processor...")
    finally:
        processor.stop()

def main():
    """Main entry point: Start both HTTP server and batch processor"""
    # Start batch processor in background thread
    processor_thread = threading.Thread(target=run_batch_processor, daemon=False)
    processor_thread.start()

    # Start Flask HTTP server for Cloud Run health checks
    port = int(os.getenv("PORT", "8080"))
    logger.info(f"Starting HTTP health server on port {port}")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True
    )

if __name__ == "__main__":
    main()
