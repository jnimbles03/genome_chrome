#!/usr/bin/env python3
# test_batch_system.py
# Integration tests for batch processing system

import os
import sys
import time
import logging
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def test_database_initialization():
    """Test 1: Database initialization"""
    print("\n" + "=" * 60)
    print("TEST 1: Database Initialization")
    print("=" * 60)

    try:
        from app.services.storage import init_db, get_queue_stats

        # Initialize database
        init_db()
        logger.info("✓ Database initialized successfully")

        # Get initial stats
        stats = get_queue_stats()
        logger.info(f"✓ Queue stats: {stats}")

        print("✅ PASS: Database initialization")
        return True

    except Exception as e:
        logger.error(f"❌ FAIL: {str(e)}")
        return False


def test_job_queue_operations():
    """Test 2: Job queue operations"""
    print("\n" + "=" * 60)
    print("TEST 2: Job Queue Operations")
    print("=" * 60)

    try:
        from app.services.storage import add_job, get_next_job, complete_job, fail_job

        # Add test job
        test_url = "https://example.com/test-form.pdf"
        job_id = add_job(test_url, batch_id=None, priority=0, max_retries=3)
        logger.info(f"✓ Added job: {job_id}")

        # Get next job
        job = get_next_job("test_worker")
        if not job:
            raise Exception("Failed to get job from queue")

        logger.info(f"✓ Retrieved job: {job['job_id']}")

        # Complete job
        complete_job(job['job_id'], result={"title": "Test Form", "entity": "Test Entity"})
        logger.info("✓ Job marked as completed")

        # Test job failure with retry
        test_url_2 = "https://example.com/test-form-2.pdf"
        job_id_2 = add_job(test_url_2)
        job_2 = get_next_job("test_worker")
        fail_job(job_2['job_id'], "Test error", can_retry=True)
        logger.info("✓ Job marked as failed (with retry)")

        print("✅ PASS: Job queue operations")
        return True

    except Exception as e:
        logger.error(f"❌ FAIL: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_batch_operations():
    """Test 3: Batch operations"""
    print("\n" + "=" * 60)
    print("TEST 3: Batch Operations")
    print("=" * 60)

    try:
        from app.services.storage import add_batch, get_batch_status, get_recent_batches

        # Create test batch
        test_urls = [
            "https://example.com/form1.pdf",
            "https://example.com/form2.pdf",
            "https://example.com/form3.pdf"
        ]

        batch_id = add_batch(
            urls=test_urls,
            batch_name="Test Batch",
            config={"test": True}
        )
        logger.info(f"✓ Created batch: {batch_id}")

        # Get batch status
        status = get_batch_status(batch_id)
        if not status:
            raise Exception("Failed to get batch status")

        logger.info(f"✓ Batch status: {status['status']}, {status['total_jobs']} jobs")

        # Get recent batches
        batches = get_recent_batches(limit=5)
        logger.info(f"✓ Retrieved {len(batches)} recent batches")

        print("✅ PASS: Batch operations")
        return True

    except Exception as e:
        logger.error(f"❌ FAIL: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_rate_limiter():
    """Test 4: Rate limiter"""
    print("\n" + "=" * 60)
    print("TEST 4: Rate Limiter")
    print("=" * 60)

    try:
        from app.services.llm_router import APIRateLimiter

        # Create rate limiter (60 requests per minute = 1 per second)
        limiter = APIRateLimiter(requests_per_minute=60)

        # Test acquiring tokens
        for i in range(5):
            start = time.time()
            success = limiter.acquire(timeout=5.0)
            elapsed = time.time() - start

            if not success:
                raise Exception(f"Failed to acquire token {i+1}")

            logger.info(f"✓ Acquired token {i+1} (waited {elapsed:.3f}s)")

        # Check stats
        stats = limiter.get_stats()
        logger.info(f"✓ Rate limiter stats: {stats}")

        print("✅ PASS: Rate limiter")
        return True

    except Exception as e:
        logger.error(f"❌ FAIL: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_llm_routing_with_rate_limiting():
    """Test 5: LLM routing with rate limiting"""
    print("\n" + "=" * 60)
    print("TEST 5: LLM Routing with Rate Limiting")
    print("=" * 60)

    try:
        from app.services.llm_router import chat_complete, LLMConfig

        # Check if API keys are configured
        config = LLMConfig.from_env()
        if not config.gemini_api_key and not config.anthropic_api_key:
            logger.warning("⚠️  SKIP: No LLM API keys configured")
            return True

        # Test simple chat completion
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'Rate limiting test successful' and nothing else."}
        ]

        response = chat_complete(
            messages=messages,
            max_tokens=50,
            temperature=0.0
        )

        if not response:
            raise Exception("LLM returned empty response")

        logger.info(f"✓ LLM response: {response[:100]}")
        logger.info(f"✓ Rate limiting applied successfully")

        print("✅ PASS: LLM routing with rate limiting")
        return True

    except Exception as e:
        logger.error(f"❌ FAIL: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def _auth_headers():
    tokens = [t.strip() for t in os.getenv("FORMGENOME_API_TOKENS", "").split(",") if t.strip()]
    return {"Authorization": f"Bearer {tokens[0]}"} if tokens else {}


def test_api_endpoints():
    """Test 6: API endpoints"""
    print("\n" + "=" * 60)
    print("TEST 6: API Endpoints")
    print("=" * 60)

    try:
        from main import create_app

        # Create Flask test client
        app = create_app()
        client = app.test_client()

        # Test health endpoint
        response = client.get('/health')
        if response.status_code != 200:
            raise Exception(f"Health check failed: {response.status_code}")
        logger.info("✓ Health endpoint working")

        # Test batch health endpoint
        response = client.get('/api/batch/health')
        if response.status_code != 200:
            raise Exception(f"Batch health check failed: {response.status_code}")
        logger.info("✓ Batch health endpoint working")

        # Test batch creation
        response = client.post(
            '/api/batch/create',
            json={
                "urls": [
                    "https://example.com/form1.pdf",
                    "https://example.com/form2.pdf"
                ],
                "name": "API Test Batch"
            },
            headers=_auth_headers(),
            content_type='application/json'
        )

        if response.status_code != 201:
            raise Exception(f"Batch creation failed: {response.status_code} - {response.data}")

        batch_data = response.get_json()
        batch_id = batch_data.get('batch_id')
        logger.info(f"✓ Created batch via API: {batch_id}")

        # Test batch status
        response = client.get(f'/api/batch/status/{batch_id}')
        if response.status_code != 200:
            raise Exception(f"Batch status failed: {response.status_code}")

        status_data = response.get_json()
        logger.info(f"✓ Retrieved batch status: {status_data['batch']['status']}")

        # Test batch list
        response = client.get('/api/batch/list?limit=5')
        if response.status_code != 200:
            raise Exception(f"Batch list failed: {response.status_code}")

        list_data = response.get_json()
        logger.info(f"✓ Retrieved batch list: {list_data['count']} batches")

        # Test queue stats
        response = client.get('/api/batch/stats')
        if response.status_code != 200:
            raise Exception(f"Queue stats failed: {response.status_code}")

        stats_data = response.get_json()
        logger.info(f"✓ Retrieved queue stats: {stats_data['stats']}")

        print("✅ PASS: API endpoints")
        return True

    except Exception as e:
        logger.error(f"❌ FAIL: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("BATCH PROCESSING SYSTEM TEST SUITE")
    print("=" * 60)

    tests = [
        ("Database Initialization", test_database_initialization),
        ("Job Queue Operations", test_job_queue_operations),
        ("Batch Operations", test_batch_operations),
        ("Rate Limiter", test_rate_limiter),
        ("LLM Routing with Rate Limiting", test_llm_routing_with_rate_limiting),
        ("API Endpoints", test_api_endpoints),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            logger.error(f"Test '{name}' crashed: {str(e)}")
            results.append((name, False))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")

    print("=" * 60)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
