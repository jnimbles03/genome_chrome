# app/services/storage.py
# Database-backed storage with job queue for batch processing

import os
import psycopg2
import psycopg2.extras
import json
import time
import hashlib
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager
import logging
from functools import wraps
import threading

logger = logging.getLogger(__name__)

# Global lock for database writes (per container instance)
_db_lock = threading.RLock()

# Retry decorator for database operations
def retry_on_locked(max_retries=3, delay=0.1):
    """Retry database operations on connection/lock errors (PostgreSQL doesn't need aggressive retries)"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import random
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                    if attempt < max_retries - 1:
                        sleep_time = delay * (2 ** attempt)
                        logger.warning(f"Database connection error, retrying in {sleep_time:.2f}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(sleep_time)
                    else:
                        raise
            return func(*args, **kwargs)
        return wrapper
    return decorator

# PostgreSQL connection settings
# Cloud SQL uses Unix socket when running in Cloud Run
CLOUD_SQL_CONNECTION_NAME = os.getenv("CLOUD_SQL_CONNECTION_NAME", "formgenome:us-central1:form-genome-db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASSWORD", "")  # Set via secret
DB_NAME = os.getenv("DB_NAME", "formgenome")

# =============================================================================
# Database Context Manager
# =============================================================================

@contextmanager
def get_db():
    """Get PostgreSQL database connection with context manager"""
    # Cloud SQL uses Unix sockets in Cloud Run, but TCP on Windows/Local
    if os.name == 'nt' or os.getenv('DB_HOST'):
        # Local/TCP connection
        db_host = os.getenv('DB_HOST', '127.0.0.1')
        conn = psycopg2.connect(
            host=db_host,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            port=os.getenv('DB_PORT', 5432),
            cursor_factory=psycopg2.extras.RealDictCursor
        )
    else:
        # Cloud Run (Unix Socket)
        unix_socket_path = f"/cloudsql/{CLOUD_SQL_CONNECTION_NAME}"
        conn = psycopg2.connect(
            host=unix_socket_path,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            cursor_factory=psycopg2.extras.RealDictCursor
        )

    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# =============================================================================
# Schema Initialization
# =============================================================================

def init_db():
    """Initialize PostgreSQL database schema"""
    with get_db() as conn:
        cursor = conn.cursor()

        # Forms table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS forms (
                id SERIAL PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                url_hash TEXT UNIQUE NOT NULL,
                title TEXT,
                entity_name TEXT,
                complexity INTEGER,
                status TEXT DEFAULT 'pending',
                created_at BIGINT,
                updated_at BIGINT,
                analyzed_at BIGINT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                metadata TEXT
            )
        """)

        # Job queue table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS job_queue (
                id SERIAL PRIMARY KEY,
                job_id TEXT UNIQUE NOT NULL,
                batch_id TEXT,
                url TEXT NOT NULL,
                url_hash TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                priority INTEGER DEFAULT 0,
                created_at BIGINT,
                started_at BIGINT,
                completed_at BIGINT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                worker_id TEXT,
                result TEXT
            )
        """)

        # Batch jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS batch_jobs (
                id SERIAL PRIMARY KEY,
                batch_id TEXT UNIQUE NOT NULL,
                name TEXT,
                total_jobs INTEGER DEFAULT 0,
                completed_jobs INTEGER DEFAULT 0,
                failed_jobs INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                created_at BIGINT,
                completed_at BIGINT,
                config TEXT,
                discovery_status TEXT,
                discovered_count INTEGER DEFAULT 0
            )
        """)

        # Create indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_forms_url_hash ON forms(url_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_forms_status ON forms(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_forms_entity ON forms(entity_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON job_queue(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_batch ON job_queue(batch_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_priority ON job_queue(priority DESC, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_batch_id ON batch_jobs(batch_id)")

        conn.commit()
        logger.info("PostgreSQL database initialized successfully")


# =============================================================================
# Job Queue Operations
# =============================================================================

@retry_on_locked()  # Use default aggressive retry (15 retries, 0.5s initial delay)
def add_job(url: str, batch_id: Optional[str] = None, priority: int = 0, max_retries: int = 3) -> str:
    """
    Add a job to the queue (with deduplication)

    Returns:
        job_id if added, None if already exists
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()
    job_id = f"job_{url_hash}_{int(time.time())}"

    with get_db() as conn:
        cursor = conn.cursor()

        # Check if already in queue
        cursor.execute(
            "SELECT job_id FROM job_queue WHERE url_hash = %s AND status IN ('pending', 'processing')",
            (url_hash,)
        )
        existing = cursor.fetchone()
        if existing:
            logger.debug(f"Job already in queue: {url}")
            return existing['job_id']

        # Add to queue
        cursor.execute("""
            INSERT INTO job_queue (job_id, batch_id, url, url_hash, status, priority, created_at, max_retries)
            VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s)
        """, (job_id, batch_id, url, url_hash, priority, int(time.time()), max_retries))

        logger.info(f"Added job to queue: {job_id} ({url})")
        return job_id


@retry_on_locked()  # Use default aggressive retry
def add_batch(urls: List[str], batch_name: Optional[str] = None, config: Optional[Dict] = None, discovery_status: Optional[str] = None) -> str:
    """
    Add a batch of jobs to the queue

    Returns:
        batch_id
    """
    batch_id = f"batch_{int(time.time())}_{hashlib.md5(str(urls).encode()).hexdigest()[:8]}"

    with get_db() as conn:
        cursor = conn.cursor()

        # Create batch record
        cursor.execute("""
            INSERT INTO batch_jobs (batch_id, name, total_jobs, status, created_at, config, discovery_status, discovered_count)
            VALUES (%s, %s, %s, 'running', %s, %s, %s, %s)
        """, (batch_id, batch_name, len(urls), int(time.time()), json.dumps(config or {}), discovery_status, len(urls) if discovery_status == 'completed' else 0))

        # Add jobs
        job_ids = []
        for url in urls:
            job_id = add_job(url, batch_id=batch_id)
            if job_id:
                job_ids.append(job_id)

        logger.info(f"Created batch {batch_id} with {len(job_ids)} jobs")
        return batch_id


@retry_on_locked()  # Use default aggressive retry
def create_discovering_batch(batch_name: str, config: Optional[Dict] = None) -> str:
    """
    Create a batch in 'discovering' state (before URLs are known)

    Returns:
        batch_id
    """
    batch_id = f"batch_{int(time.time())}_{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO batch_jobs (batch_id, name, total_jobs, status, created_at, config, discovery_status, discovered_count)
            VALUES (%s, %s, 0, 'discovering', %s, %s, 'in_progress', 0)
        """, (batch_id, batch_name, int(time.time()), json.dumps(config or {})))

    logger.info(f"Created discovering batch {batch_id}")
    return batch_id


@retry_on_locked()  # Use default aggressive retry
def update_discovery_progress(batch_id: str, discovered_count: int, status: str = 'in_progress'):
    """Update discovery progress for a batch"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE batch_jobs
            SET discovered_count = %s, discovery_status = %s
            WHERE batch_id = %s
        """, (discovered_count, status, batch_id))


@retry_on_locked()  # Use default aggressive retry
def finalize_discovery(batch_id: str, urls: List[str]):
    """Finalize discovery by adding jobs and updating batch status"""
    with _db_lock:  # Serialize writes within this container
        with get_db() as conn:
            cursor = conn.cursor()

            try:
                # Bulk insert jobs (much faster than individual inserts)
                timestamp = int(time.time())
                job_data = []
                for url in urls:
                    url_hash = hashlib.md5(url.encode()).hexdigest()
                    job_id = f"job_{url_hash}_{timestamp}"
                    job_data.append((job_id, batch_id, url, url_hash, 'pending', 0, timestamp, 3))

                # Insert all jobs in a single transaction (PostgreSQL syntax)
                cursor.executemany("""
                    INSERT INTO job_queue (job_id, batch_id, url, url_hash, status, priority, created_at, max_retries)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id) DO NOTHING
                """, job_data)

                # Update batch
                cursor.execute("""
                    UPDATE batch_jobs
                    SET total_jobs = %s, discovered_count = %s, discovery_status = 'completed', status = 'running'
                    WHERE batch_id = %s
                """, (len(urls), len(urls), batch_id))

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e

    logger.info(f"Finalized discovery for batch {batch_id} with {len(urls)} forms")


def get_next_job(worker_id: str) -> Optional[Dict]:
    """
    Get next pending job and mark as processing

    Returns:
        Job dict or None
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Get highest priority pending job
        cursor.execute("""
            SELECT * FROM job_queue
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
        """)

        row = cursor.fetchone()
        if not row:
            return None

        job = dict(row)

        # Mark as processing
        cursor.execute("""
            UPDATE job_queue
            SET status = 'processing', started_at = %s, worker_id = %s
            WHERE job_id = %s
        """, (int(time.time()), worker_id, job['job_id']))

        logger.info(f"Worker {worker_id} claimed job {job['job_id']}")
        return job


def complete_job(job_id: str, result: Optional[Dict] = None):
    """Mark job as completed"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE job_queue
            SET status = 'completed', completed_at = %s, result = %s
            WHERE job_id = %s
        """, (int(time.time()), json.dumps(result) if result else None, job_id))

        # Update batch progress
        cursor.execute("SELECT batch_id FROM job_queue WHERE job_id = %s", (job_id,))
        batch_id = cursor.fetchone()
        if batch_id:
            _update_batch_progress(cursor, batch_id['batch_id'])

        logger.info(f"Job completed: {job_id}")


def fail_job(job_id: str, error: str, can_retry: bool = True):
    """Mark job as failed (with retry logic)"""
    with get_db() as conn:
        cursor = conn.cursor()

        # Get current retry count
        cursor.execute("SELECT retry_count, max_retries FROM job_queue WHERE job_id = %s", (job_id,))
        row = cursor.fetchone()
        if not row:
            return

        retry_count, max_retries = row['retry_count'], row['max_retries']
        retry_count += 1

        # Determine if should retry
        if can_retry and retry_count < max_retries:
            # Reset to pending for retry
            cursor.execute("""
                UPDATE job_queue
                SET status = 'pending', retry_count = %s, error_message = %s, worker_id = NULL
                WHERE job_id = %s
            """, (retry_count, error, job_id))
            logger.warning(f"Job {job_id} failed (retry {retry_count}/{max_retries}): {error}")
        else:
            # Mark as failed
            cursor.execute("""
                UPDATE job_queue
                SET status = 'failed', completed_at = %s, error_message = %s, retry_count = %s
                WHERE job_id = %s
            """, (int(time.time()), error, retry_count, job_id))

            # Update batch progress
            cursor.execute("SELECT batch_id FROM job_queue WHERE job_id = %s", (job_id,))
            batch_id = cursor.fetchone()
            if batch_id:
                _update_batch_progress(cursor, batch_id['batch_id'])

            logger.error(f"Job {job_id} permanently failed: {error}")


def _update_batch_progress(cursor, batch_id: str):
    """Update batch job progress"""
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
        FROM job_queue
        WHERE batch_id = %s
    """, (batch_id,))

    row = cursor.fetchone()
    total, completed, failed = row['total'], row['completed'] or 0, row['failed'] or 0

    # Update batch record
    status = 'running'
    if completed + failed == total:
        status = 'completed'

    cursor.execute("""
        UPDATE batch_jobs
        SET completed_jobs = %s, failed_jobs = %s, status = %s, completed_at = %s
        WHERE batch_id = %s
    """, (completed, failed, status, int(time.time()) if status == 'completed' else None, batch_id))


# =============================================================================
# Progress & Status Queries
# =============================================================================

def get_batch_status(batch_id: str) -> Optional[Dict]:
    """Get batch job status"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM batch_jobs WHERE batch_id = %s", (batch_id,))
        row = cursor.fetchone()
        if not row:
            return None

        batch = dict(row)

        # Get job breakdown
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM job_queue
            WHERE batch_id = %s
            GROUP BY status
        """, (batch_id,))

        breakdown = {row['status']: row['count'] for row in cursor.fetchall()}
        batch['job_breakdown'] = breakdown

        # Calculate progress
        total = batch['total_jobs']
        completed = batch['completed_jobs']
        batch['progress'] = (completed / total * 100) if total > 0 else 0

        # Add discovered count for UI progress display
        # Use discovered_count from database (updated during discovery)
        batch['discovered'] = batch.get('discovered_count', total)

        return batch


def get_queue_stats() -> Dict:
    """Get overall queue statistics"""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM job_queue
            GROUP BY status
        """)

        stats = {row['status']: row['count'] for row in cursor.fetchall()}
        stats['total'] = sum(stats.values())

        return stats


def get_recent_batches(limit: int = 10) -> List[Dict]:
    """Get recent batch jobs"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM batch_jobs
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))

        return [dict(row) for row in cursor.fetchall()]


# =============================================================================
# Form Storage Operations
# =============================================================================

def save_form(url: str, title: str, entity_name: Optional[str], complexity: int, metadata: Optional[Dict] = None):
    """Save analyzed form to database"""
    url_hash = hashlib.md5(url.encode()).hexdigest()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO forms (url, url_hash, title, entity_name, complexity, status, analyzed_at, updated_at, metadata)
            VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, %s)
            ON CONFLICT (url_hash) DO UPDATE SET
                title = EXCLUDED.title,
                entity_name = EXCLUDED.entity_name,
                complexity = EXCLUDED.complexity,
                status = EXCLUDED.status,
                analyzed_at = EXCLUDED.analyzed_at,
                updated_at = EXCLUDED.updated_at,
                metadata = EXCLUDED.metadata
        """, (url, url_hash, title, entity_name, complexity, int(time.time()), int(time.time()), json.dumps(metadata) if metadata else None))

        logger.info(f"Saved form: {title} ({url})")


def get_form(url: str) -> Optional[Dict]:
    """Get form by URL"""
    url_hash = hashlib.md5(url.encode()).hexdigest()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM forms WHERE url_hash = %s", (url_hash,))
        row = cursor.fetchone()
        return dict(row) if row else None


def form_exists(url: str) -> bool:
    """Check if form already analyzed"""
    return get_form(url) is not None


# =============================================================================
# Cleanup Operations
# =============================================================================

def cleanup_stale_jobs(timeout_seconds: int = 3600):
    """Mark stale processing jobs as failed"""
    with get_db() as conn:
        cursor = conn.cursor()
        cutoff = int(time.time()) - timeout_seconds

        cursor.execute("""
            UPDATE job_queue
            SET status = 'failed', error_message = 'Job timeout (stale)'
            WHERE status = 'processing' AND started_at < %s
        """, (cutoff,))

        affected = cursor.rowcount
        if affected > 0:
            logger.warning(f"Cleaned up {affected} stale jobs")

        return affected


# init_db()  <-- Removed to prevent import-time execution crash
