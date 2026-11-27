# app/services/batch_processor.py
# Batch job processor with rate limiting and parallel workers
# Processes forms from job queue with durable retry logic

import os
import sys
import time
import logging
import threading
import hashlib
from io import BytesIO
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from collections import deque
import signal
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# PDF processing
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

# Local imports
from app.services.storage import (
    get_next_job, complete_job, fail_job, get_queue_stats,
    save_form, cleanup_stale_jobs
)
from app.services.title_llm import make_title
from app.services.llm_router import LLMError
from app.services.remote_fetch import open_stream, read_capped

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

@dataclass
class BatchConfig:
    """Batch processing configuration"""
    # Worker settings
    num_workers: int = 3
    worker_poll_interval: float = 2.0  # seconds between job polls
    worker_shutdown_timeout: float = 30.0  # seconds to wait for graceful shutdown

    # Rate limiting (prevent LLM API 429 errors)
    rate_limit_enabled: bool = True
    requests_per_minute: int = 30  # Conservative limit for LLM APIs

    # PDF processing
    pdf_timeout: float = 30.0  # seconds to download PDF
    pdf_max_size: int = 50 * 1024 * 1024  # 50MB max

    # Retry settings
    max_retries: int = 3
    retry_backoff_base: float = 2.0  # exponential backoff multiplier

    # Cleanup settings
    stale_job_timeout: int = 3600  # 1 hour
    cleanup_interval: int = 300  # 5 minutes

    @classmethod
    def from_env(cls) -> 'BatchConfig':
        """Load configuration from environment variables"""
        return cls(
            num_workers=int(os.getenv("BATCH_NUM_WORKERS", "3")),
            worker_poll_interval=float(os.getenv("BATCH_POLL_INTERVAL", "2.0")),
            requests_per_minute=int(os.getenv("BATCH_RATE_LIMIT_RPM", "30")),
            rate_limit_enabled=os.getenv("BATCH_RATE_LIMIT", "true").lower() == "true",
            pdf_timeout=float(os.getenv("BATCH_PDF_TIMEOUT", "30.0")),
            pdf_max_size=int(os.getenv("BATCH_PDF_MAX_SIZE", str(50 * 1024 * 1024))),
            max_retries=int(os.getenv("BATCH_MAX_RETRIES", "3")),
            stale_job_timeout=int(os.getenv("BATCH_STALE_TIMEOUT", "3600")),
            cleanup_interval=int(os.getenv("BATCH_CLEANUP_INTERVAL", "300"))
        )


# =============================================================================
# Rate Limiter
# =============================================================================

class RateLimiter:
    """
    Token bucket rate limiter for API calls.

    Prevents overwhelming LLM APIs with too many concurrent requests.
    """

    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute  # seconds between requests
        self.timestamps: deque = deque(maxlen=requests_per_minute)
        self.lock = threading.Lock()

    def acquire(self, timeout: float = 60.0) -> bool:
        """
        Acquire permission to make a request.
        Blocks until rate limit allows, or timeout expires.

        Returns:
            True if acquired, False if timeout
        """
        start_time = time.time()

        while True:
            with self.lock:
                now = time.time()

                # Remove timestamps older than 1 minute
                while self.timestamps and now - self.timestamps[0] > 60.0:
                    self.timestamps.popleft()

                # Check if we can proceed
                if len(self.timestamps) < self.requests_per_minute:
                    self.timestamps.append(now)
                    logger.debug(f"Rate limiter: acquired ({len(self.timestamps)}/{self.requests_per_minute})")
                    return True

                # Calculate wait time
                oldest = self.timestamps[0]
                wait_time = 60.0 - (now - oldest)

            # Check timeout
            if time.time() - start_time > timeout:
                logger.warning("Rate limiter: timeout waiting for slot")
                return False

            # Wait before retrying
            sleep_time = min(wait_time + 0.1, 1.0)  # Wait at least wait_time, max 1 second
            logger.debug(f"Rate limiter: waiting {sleep_time:.2f}s ({len(self.timestamps)}/{self.requests_per_minute})")
            time.sleep(sleep_time)


# =============================================================================
# PDF Processing
# =============================================================================

def download_pdf(url: str, timeout: float = 30.0, max_size: int = 50*1024*1024) -> bytes:
    """
    Download PDF from URL with size limits.

    Args:
        url: PDF URL
        timeout: Download timeout in seconds
        max_size: Maximum PDF size in bytes

    Returns:
        PDF content as bytes

    Raises:
        Exception: If download fails or PDF too large
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    logger.info(f"Downloading PDF: {url}")
    response = open_stream(url, headers=headers, timeout=timeout)
    try:
        response.raise_for_status()
        pdf_bytes = read_capped(response, max_size)
    finally:
        response.close()
    logger.info(f"Downloaded PDF: {len(pdf_bytes)} bytes")
    return pdf_bytes


def extract_first_page_text(pdf_bytes: bytes) -> str:
    """
    Extract text from first page of PDF.

    Args:
        pdf_bytes: PDF content

    Returns:
        First page text (max 2000 chars)
    """
    if not PdfReader:
        raise ImportError("pypdf not installed. Run: pip install pypdf")

    try:
        pdf = PdfReader(BytesIO(pdf_bytes))
        if len(pdf.pages) == 0:
            raise ValueError("PDF has no pages")

        first_page = pdf.pages[0]
        text = first_page.extract_text() or ""

        # Truncate to reasonable length
        if len(text) > 2000:
            text = text[:2000]

        logger.debug(f"Extracted {len(text)} chars from first page")
        return text

    except Exception as e:
        logger.error(f"PDF text extraction failed: {str(e)}")
        raise


def calculate_complexity(pdf_bytes: bytes, text: str) -> int:
    """
    Calculate form complexity score (0-100).

    Factors:
    - Number of pages
    - Text density
    - PDF size

    Args:
        pdf_bytes: PDF content
        text: Extracted text from first page

    Returns:
        Complexity score (0-100)
    """
    try:
        pdf = PdfReader(BytesIO(pdf_bytes))
        num_pages = len(pdf.pages)
        pdf_size = len(pdf_bytes)
        text_length = len(text)

        # Base complexity on pages
        complexity = min(num_pages * 5, 40)  # 0-40 based on pages

        # Add complexity for size
        if pdf_size > 5 * 1024 * 1024:  # > 5MB
            complexity += 20
        elif pdf_size > 1 * 1024 * 1024:  # > 1MB
            complexity += 10

        # Add complexity for text density (sparse text = complex form)
        if text_length < 100:
            complexity += 30
        elif text_length < 500:
            complexity += 20
        elif text_length < 1000:
            complexity += 10

        return min(complexity, 100)

    except Exception:
        return 50  # Default medium complexity


# =============================================================================
# Job Processing
# =============================================================================

def process_job(job: Dict[str, Any], config: BatchConfig) -> Dict[str, Any]:
    """
    Process a single job: download PDF, extract title/entity, save to database.

    Args:
        job: Job dict from queue
        config: Batch configuration

    Returns:
        Result dict with title, entity, complexity

    Raises:
        Exception: If processing fails
    """
    job_id = job['job_id']
    url = job['url']

    logger.info(f"Processing job {job_id}: {url}")

    # Step 1: Download PDF
    try:
        pdf_bytes = download_pdf(url, timeout=config.pdf_timeout, max_size=config.pdf_max_size)
    except Exception as e:
        logger.error(f"Job {job_id}: PDF download failed: {str(e)}")
        raise Exception(f"Download failed: {str(e)}")

    # Step 2: Extract first page text
    try:
        first_page_text = extract_first_page_text(pdf_bytes)
    except Exception as e:
        logger.error(f"Job {job_id}: Text extraction failed: {str(e)}")
        raise Exception(f"Text extraction failed: {str(e)}")

    # Step 3: Calculate complexity
    try:
        complexity = calculate_complexity(pdf_bytes, first_page_text)
        logger.info(f"Job {job_id}: Complexity = {complexity}")
    except Exception as e:
        logger.warning(f"Job {job_id}: Complexity calculation failed: {str(e)}")
        complexity = 50  # Default

    # Step 4: Full Analysis using Two-Stage LLM (Gemini -> Claude)
    try:
        # Import here to avoid circular dependencies if any
        from app.api.analyze import analyze_pdf_file 
        
        # We need a temporary file path for analyze_pdf_file because it uses PdfReader on a path
        # But we have bytes. Let's write to temp file.
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tf:
            tf.write(pdf_bytes)
            tf.flush()
            temp_path = tf.name
        
        try:
            analysis_result = analyze_pdf_file(temp_path, os.path.basename(url), url=url)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        if not analysis_result.get('success'):
            raise Exception(f"Analysis failed: {analysis_result.get('error')}")

        title = analysis_result.get('title')
        entity_name = analysis_result.get('entity')
        complexity = analysis_result.get('complexity', 50)
        
        logger.info(f"Job {job_id}: Title='{title}', Entity='{entity_name}'")

    except Exception as e:
        logger.error(f"Job {job_id}: Analysis failed: {str(e)}")
        raise Exception(f"Analysis failed: {str(e)}")

    # Step 5: Save to database
    try:
        # Merge analysis results into metadata
        metadata = analysis_result.copy()
        # Remove keys that are stored in main columns to avoid duplication/confusion
        for k in ['title', 'entity', 'complexity', 'fileName', 'fileType', 'preview', 'success', 'error', 'pages']:
            metadata.pop(k, None)
            
        # Ensure we keep the original PDF stats
        metadata['pdf_size'] = len(pdf_bytes)
        metadata['text_length'] = len(first_page_text)

        save_form(
            url=url,
            title=title,
            entity_name=entity_name,
            complexity=complexity,
            metadata=metadata
        )

        logger.info(f"Job {job_id}: Saved to database")

    except Exception as e:
        logger.error(f"Job {job_id}: Database save failed: {str(e)}")
        raise Exception(f"Database save failed: {str(e)}")

    # Return result
    return {
        "title": title,
        "entity_name": entity_name,
        "complexity": complexity,
        "pdf_size": len(pdf_bytes),
        "text_length": len(first_page_text),
        "analysis": metadata
    }


# =============================================================================
# Worker Process
# =============================================================================

class Worker:
    """
    Job queue worker with rate limiting and graceful shutdown.
    """

    def __init__(self, worker_id: str, config: BatchConfig, rate_limiter: Optional[RateLimiter] = None):
        self.worker_id = worker_id
        self.config = config
        self.rate_limiter = rate_limiter
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.jobs_processed = 0
        self.jobs_failed = 0

    def start(self):
        """Start worker in background thread"""
        if self.running:
            logger.warning(f"Worker {self.worker_id} already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=False)
        self.thread.start()
        logger.info(f"Worker {self.worker_id} started")

    def stop(self, timeout: float = 30.0):
        """Stop worker gracefully"""
        if not self.running:
            return

        logger.info(f"Worker {self.worker_id} stopping...")
        self.running = False

        if self.thread:
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                logger.warning(f"Worker {self.worker_id} did not stop gracefully")
            else:
                logger.info(f"Worker {self.worker_id} stopped (processed={self.jobs_processed}, failed={self.jobs_failed})")

    def _run(self):
        """Main worker loop"""
        logger.info(f"Worker {self.worker_id} running")

        while self.running:
            try:
                # Get next job from queue
                job = get_next_job(self.worker_id)

                if not job:
                    # No jobs available, sleep and retry
                    time.sleep(self.config.worker_poll_interval)
                    continue

                job_id = job['job_id']
                logger.info(f"Worker {self.worker_id} processing job {job_id}")

                # Rate limiting (if enabled)
                if self.config.rate_limit_enabled and self.rate_limiter:
                    if not self.rate_limiter.acquire(timeout=60.0):
                        logger.error(f"Worker {self.worker_id}: Rate limiter timeout for job {job_id}")
                        fail_job(job_id, "Rate limiter timeout", can_retry=True)
                        self.jobs_failed += 1
                        continue

                # Process job
                try:
                    result = process_job(job, self.config)
                    complete_job(job_id, result)
                    self.jobs_processed += 1
                    logger.info(f"Worker {self.worker_id}: Job {job_id} completed successfully")

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Worker {self.worker_id}: Job {job_id} failed: {error_msg}")

                    # Determine if retryable
                    can_retry = True
                    if "404" in error_msg or "not found" in error_msg.lower():
                        can_retry = False  # Don't retry 404s

                    fail_job(job_id, error_msg, can_retry=can_retry)
                    self.jobs_failed += 1

            except KeyboardInterrupt:
                logger.info(f"Worker {self.worker_id} interrupted")
                break

            except Exception as e:
                logger.error(f"Worker {self.worker_id} unexpected error: {str(e)}")
                time.sleep(5.0)  # Back off on unexpected errors

        logger.info(f"Worker {self.worker_id} exiting")


# =============================================================================
# Batch Processor Manager
# =============================================================================

class BatchProcessor:
    """
    Manages multiple workers and cleanup tasks.
    """

    def __init__(self, config: Optional[BatchConfig] = None):
        self.config = config or BatchConfig.from_env()
        self.rate_limiter = RateLimiter(self.config.requests_per_minute) if self.config.rate_limit_enabled else None
        self.workers: List[Worker] = []
        self.cleanup_thread: Optional[threading.Thread] = None
        self.running = False

        # Signal handlers for graceful shutdown (only in main thread)
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()

    def start(self):
        """Start batch processor with workers"""
        if self.running:
            logger.warning("Batch processor already running")
            return

        self.running = True

        logger.info(f"Starting batch processor with {self.config.num_workers} workers")
        logger.info(f"Rate limiting: {self.config.rate_limit_enabled} ({self.config.requests_per_minute} req/min)")

        # Start workers
        for i in range(self.config.num_workers):
            worker_id = f"worker_{i+1}"
            worker = Worker(worker_id, self.config, self.rate_limiter)
            worker.start()
            self.workers.append(worker)

        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()

        logger.info("Batch processor started")

    def stop(self):
        """Stop batch processor gracefully"""
        if not self.running:
            return

        logger.info("Stopping batch processor...")
        self.running = False

        # Stop all workers
        for worker in self.workers:
            worker.stop(timeout=self.config.worker_shutdown_timeout)

        logger.info("Batch processor stopped")

    def wait(self):
        """Wait for all workers to finish (blocking)"""
        try:
            while self.running:
                time.sleep(1.0)

                # Check worker status
                alive = sum(1 for w in self.workers if w.thread and w.thread.is_alive())
                if alive == 0 and self.running:
                    logger.warning("All workers stopped unexpectedly")
                    break

        except KeyboardInterrupt:
            logger.info("Interrupted, stopping...")
            self.stop()

    def _cleanup_loop(self):
        """Periodic cleanup of stale jobs"""
        logger.info("Cleanup thread started")

        while self.running:
            try:
                time.sleep(self.config.cleanup_interval)

                if not self.running:
                    break

                # Cleanup stale jobs
                cleaned = cleanup_stale_jobs(timeout_seconds=self.config.stale_job_timeout)
                if cleaned > 0:
                    logger.info(f"Cleaned up {cleaned} stale jobs")

            except Exception as e:
                logger.error(f"Cleanup error: {str(e)}")

        logger.info("Cleanup thread stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get processor statistics"""
        queue_stats = get_queue_stats()

        worker_stats = []
        for worker in self.workers:
            worker_stats.append({
                "worker_id": worker.worker_id,
                "running": worker.running,
                "processed": worker.jobs_processed,
                "failed": worker.jobs_failed
            })

        return {
            "running": self.running,
            "num_workers": len(self.workers),
            "queue": queue_stats,
            "workers": worker_stats
        }


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    """Run batch processor from command line"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

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
    from app.services.storage import init_db
    init_db()

    # Create and start processor
    processor = BatchProcessor(config)
    processor.start()

    # Wait for completion or interrupt
    try:
        processor.wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        processor.stop()

    # Final stats
    stats = processor.get_stats()
    logger.info("=" * 60)
    logger.info("Final Statistics:")
    logger.info(f"  Queue: {stats['queue']}")
    logger.info(f"  Workers: {sum(w['processed'] for w in stats['workers'])} processed, {sum(w['failed'] for w in stats['workers'])} failed")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
