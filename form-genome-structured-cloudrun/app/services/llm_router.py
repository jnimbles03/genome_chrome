# app/services/llm_router.py
# Two-tier LLM routing: Gemini Flash (cheap/fast) → Claude Sonnet (fallback for edge cases)

from __future__ import annotations
import os
import time
import json
import logging
import threading
import contextvars
from contextlib import contextmanager
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# LLM Error Class
# -----------------------------------------------------------------------------
class LLMError(Exception):
    """Raised when LLM call fails"""
    pass

# -----------------------------------------------------------------------------
# Rate Limiter
# -----------------------------------------------------------------------------
class APIRateLimiter:
    """
    Thread-safe rate limiter for API calls.
    Uses token bucket algorithm to prevent overwhelming LLM APIs.
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
                    return True

                # Calculate wait time
                oldest = self.timestamps[0]
                wait_time = 60.0 - (now - oldest)

            # Check timeout
            if time.time() - start_time > timeout:
                logger.warning(f"Rate limiter timeout after {timeout}s")
                return False

            # Wait before retrying
            sleep_time = min(wait_time + 0.1, 1.0)  # Wait at least wait_time, max 1 second
            time.sleep(sleep_time)

    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        with self.lock:
            now = time.time()
            # Count requests in last minute
            recent = sum(1 for ts in self.timestamps if now - ts <= 60.0)
            return {
                "requests_per_minute_limit": self.requests_per_minute,
                "requests_last_minute": recent,
                "available_slots": max(0, self.requests_per_minute - recent)
            }


# -----------------------------------------------------------------------------
# Per-client LLM Budget
# -----------------------------------------------------------------------------
class ClientLLMBudget:
    """Sliding-window cap on LLM provider calls per authenticated client.

    A request reserves the calls it needs up front; each provider call is then
    charged against that reservation, and unused slots are released when the
    request ends. Reservations count against the window while held, so
    concurrent requests from one client cannot together exceed the cap.
    """

    def __init__(self, calls_per_minute: int, window_seconds: float = 60.0):
        self.calls_per_minute = calls_per_minute
        self.window_seconds = window_seconds
        self._calls: Dict[str, deque] = {}
        self._held: Dict[str, int] = {}
        self._lock = threading.Lock()

    def _used(self, client_id: str, now: float) -> int:
        calls = self._calls.setdefault(client_id, deque())
        while calls and now - calls[0] > self.window_seconds:
            calls.popleft()
        return len(calls) + self._held.get(client_id, 0)

    def reserve(self, client_id: str, calls: int) -> Optional["LLMReservation"]:
        with self._lock:
            if self._used(client_id, time.time()) + calls > self.calls_per_minute:
                return None
            self._held[client_id] = self._held.get(client_id, 0) + calls
            return LLMReservation(self, client_id, calls)

    def _charge(self, reservation: "LLMReservation") -> bool:
        with self._lock:
            now = time.time()
            client_id = reservation.client_id
            if reservation.remaining > 0:
                reservation.remaining -= 1
                self._held[client_id] -= 1
            elif self._used(client_id, now) >= self.calls_per_minute:
                return False
            self._calls.setdefault(client_id, deque()).append(now)
            return True

    def _release(self, reservation: "LLMReservation") -> None:
        with self._lock:
            self._held[reservation.client_id] -= reservation.remaining
            reservation.remaining = 0


class LLMReservation:
    def __init__(self, budget: ClientLLMBudget, client_id: str, calls: int):
        self.budget = budget
        self.client_id = client_id
        self.remaining = calls

    def charge(self) -> bool:
        return self.budget._charge(self)

    def release(self) -> None:
        self.budget._release(self)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
@dataclass
class LLMConfig:
    """LLM configuration from environment variables"""
    # Tier 1: Gemini Flash (default)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"

    # Tier 2: Claude Opus (fallback)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-opus-20240229"

    # Fallback triggers
    enable_fallback: bool = True
    confidence_threshold: float = 0.7  # Fall back if confidence < this
    complexity_threshold: int = 75  # Fall back for forms with complexity > this

    # Rate limiting
    enable_rate_limiting: bool = True
    gemini_rpm: int = 60  # Gemini requests per minute
    anthropic_rpm: int = 50  # Claude requests per minute
    rate_limit_timeout: float = 60.0  # Max seconds to wait for rate limit

    @classmethod
    def from_env(cls) -> LLMConfig:
        """Load configuration from app.core.config"""
        from app.core.config import Config
        return cls(
            gemini_api_key=Config.GEMINI_API_KEY,
            gemini_model=Config.GEMINI_MODEL,
            anthropic_api_key=Config.ANTHROPIC_API_KEY,
            anthropic_model=Config.ANTHROPIC_MODEL,
            enable_fallback=Config.ENABLE_LLM_FALLBACK,
            confidence_threshold=Config.LLM_CONFIDENCE_THRESHOLD,
            complexity_threshold=Config.LLM_COMPLEXITY_THRESHOLD,
            enable_rate_limiting=Config.ENABLE_LLM_RATE_LIMITING,
            gemini_rpm=Config.GEMINI_RPM,
            anthropic_rpm=Config.ANTHROPIC_RPM,
            rate_limit_timeout=Config.LLM_RATE_LIMIT_TIMEOUT
        )


# -----------------------------------------------------------------------------
# Global Rate Limiters (initialized on first use)
# -----------------------------------------------------------------------------
_gemini_rate_limiter: Optional[APIRateLimiter] = None
_anthropic_rate_limiter: Optional[APIRateLimiter] = None
_rate_limiter_lock = threading.Lock()


def _get_gemini_rate_limiter(config: LLMConfig) -> Optional[APIRateLimiter]:
    """Get or create global Gemini rate limiter"""
    global _gemini_rate_limiter
    if not config.enable_rate_limiting:
        return None

    with _rate_limiter_lock:
        if _gemini_rate_limiter is None:
            _gemini_rate_limiter = APIRateLimiter(config.gemini_rpm)
            logger.info(f"Initialized Gemini rate limiter: {config.gemini_rpm} req/min")
        return _gemini_rate_limiter


def _get_anthropic_rate_limiter(config: LLMConfig) -> Optional[APIRateLimiter]:
    """Get or create global Anthropic rate limiter"""
    global _anthropic_rate_limiter
    if not config.enable_rate_limiting:
        return None

    with _rate_limiter_lock:
        if _anthropic_rate_limiter is None:
            _anthropic_rate_limiter = APIRateLimiter(config.anthropic_rpm)
            logger.info(f"Initialized Anthropic rate limiter: {config.anthropic_rpm} req/min")
        return _anthropic_rate_limiter


_client_budget: Optional[ClientLLMBudget] = None
_active_reservation: contextvars.ContextVar[Optional[LLMReservation]] = contextvars.ContextVar(
    "llm_active_reservation", default=None
)


def _get_client_budget() -> ClientLLMBudget:
    global _client_budget
    from app.core.config import Config
    with _rate_limiter_lock:
        if _client_budget is None:
            _client_budget = ClientLLMBudget(Config.CLIENT_LLM_CALLS_PER_MINUTE)
        return _client_budget


@contextmanager
def client_llm_budget(client_id: str, calls: int):
    """Reserve ``calls`` LLM calls for ``client_id`` and bind them to this context.

    Yields False (binding nothing) when the client's budget cannot cover the
    reservation. Every chat_complete call made inside the block is charged to
    this reservation; unused slots are released on exit.
    """
    reservation = _get_client_budget().reserve(client_id, calls)
    if reservation is None:
        yield False
        return
    token = _active_reservation.set(reservation)
    try:
        yield True
    finally:
        _active_reservation.reset(token)
        reservation.release()


def _charge_client_budget() -> None:
    reservation = _active_reservation.get()
    if reservation is not None:
        if not reservation.charge():
            raise LLMError("Per-client LLM budget exhausted")
        return
    try:
        from flask import has_request_context
    except ImportError:
        return
    if has_request_context():
        # HTTP handlers must bind a client reservation before reaching the LLM.
        raise LLMError("LLM call from a request without a client budget")

# -----------------------------------------------------------------------------
# Provider Implementations
# -----------------------------------------------------------------------------
def _call_gemini(
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
    api_key: str
) -> str:
    """Call Google Gemini API"""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise LLMError("google-genai package not installed. Run: pip install google-genai")

    if not api_key:
        raise LLMError("GEMINI_API_KEY not configured")

    # Initialize client
    client = genai.Client(api_key=api_key)

    # Convert messages to Gemini format (Single prompt string strategy for now)
    prompt_parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            prompt_parts.append(f"Instructions: {content}")
        elif role == "user":
            prompt_parts.append(content)
        elif role == "assistant":
            prompt_parts.append(f"Previous response: {content}")

    full_prompt = "\n\n".join(prompt_parts)

    # Generate response
    try:
        response = client.models.generate_content(
            model=model,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=temperature
            )
        )
    except Exception as e:
            raise LLMError(f"Gemini API call failed: {str(e)}")

    if not response:
        raise LLMError("Gemini returned empty response")

    # Handle text extraction using new SDK attributes
    try:
        return response.text.strip()
    except Exception:
            raise LLMError("Gemini returned no text content")


def _call_anthropic(
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
    api_key: str
) -> str:
    """Call Anthropic Claude API"""
    try:
        import anthropic
    except ImportError:
        raise LLMError("anthropic package not installed. Run: pip install anthropic")

    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY not configured")

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # Separate system message from other messages
        system_msg = ""
        chat_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_msg = msg.get("content", "")
            else:
                chat_messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

        # Call Claude
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_msg if system_msg else anthropic.NOT_GIVEN,
            messages=chat_messages
        )

        if not response or not response.content:
            raise LLMError("Claude returned empty response")

        # Extract text from response
        text = ""
        for block in response.content:
            if hasattr(block, 'text'):
                text += block.text

        return text.strip()

    except Exception as e:
        raise LLMError(f"Anthropic API error: {str(e)}")


# -----------------------------------------------------------------------------
# Response Quality Assessment
# -----------------------------------------------------------------------------
def _assess_response_quality(response: str, context: Optional[Dict[str, Any]] = None) -> Tuple[bool, float, str]:
    """
    Assess if response needs fallback to higher-tier LLM.

    Returns:
        (needs_fallback, confidence, reason)
    """
    response = response.strip()

    # Check 1: Empty or too short
    if len(response) < 10:
        return True, 0.0, "response_too_short"

    # Check 2: Generic error indicators
    error_indicators = [
        "i cannot",
        "i don't have",
        "unable to",
        "error:",
        "failed to",
        "not available",
        "insufficient",
        "unclear",
        "uncertain"
    ]
    response_lower = response.lower()
    if any(indicator in response_lower for indicator in error_indicators):
        return True, 0.3, "error_indicators_found"

    # Check 3: Incomplete data (for structured outputs)
    # Look for placeholder text or missing fields
    if "unknown" in response_lower and response_lower.count("unknown") > 3:
        return True, 0.4, "too_many_unknowns"

    # Check 4: Check context-based triggers if provided
    if context:
        # High complexity forms should use Sonnet
        complexity = context.get("complexity", 0)
        if complexity > 75:
            return True, 0.5, f"high_complexity_{complexity}"

        # Poor image quality
        if context.get("poor_image_quality"):
            return True, 0.5, "poor_image_quality"

        # Explicit low confidence flag
        if context.get("low_confidence"):
            return True, 0.4, "low_confidence_flag"

    # Check 5: Response structure quality
    # For title generation, expect exactly 2 lines
    if context and context.get("task_type") == "title_generation":
        lines = [l.strip() for l in response.split("\n") if l.strip()]
        if len(lines) != 2:
            return True, 0.5, f"title_format_invalid_{len(lines)}_lines"
        if any(len(line) > 150 for line in lines):
            return True, 0.5, "title_too_long"

    # Response seems good
    return False, 0.9, "ok"


# -----------------------------------------------------------------------------
# Two-Tier Chat Completion
# -----------------------------------------------------------------------------
def chat_complete(
    messages: List[Dict[str, str]],
    *,
    max_tokens: int = 1000,
    temperature: float = 0.1,
    timeout: float = 30.0,
    context: Optional[Dict[str, Any]] = None,
    force_tier2: bool = False  # CHANGED: Default to False (Gemini First)
) -> str:
    """
    Two-tier LLM routing with automatic fallback.
    
    Tier 1: Gemini 1.5/2.5 Flash (fast, cheap, default) - MAIN DRIVER
    Tier 2: Claude 3.5 Sonnet (fallback) - DISABLED due to 404/Tier Lock
    """
    config = LLMConfig.from_env()
    
    # OVERRIDE: Force Gemini usage if force_tier2 was requested but we know Claude is dead
    if force_tier2:
        logger.warning("Tier 2 (Claude) requested but disabled due to 404s. Redirecting to Gemini.")
        force_tier2 = False

    # Track attempts for logging
    attempts = []

    # Tier 1: Gemini Flash (The Only Tier)
    tier1_response = None
    tier1_error = None

    if config.gemini_api_key:
        # Determine model based on tier
        if force_tier2:
            current_model = "gemini-1.5-pro"
            logger.info(f"LLM Tier 2 (Deep): Using {current_model} for heavy lift")
        else:
            current_model = config.gemini_model # gemini-2.5-flash
            logger.info(f"LLM Tier 1 (Scout): Using {current_model} for speed")

        _charge_client_budget()

        # Rate limiting for Gemini
        rate_limiter = _get_gemini_rate_limiter(config)
        if rate_limiter:
            logger.debug("Acquiring Gemini rate limit token...")
            if not rate_limiter.acquire(timeout=config.rate_limit_timeout):
                tier1_error = f"Gemini rate limit timeout ({config.rate_limit_timeout}s)"
                logger.warning(tier1_error)
                attempts.append(("tier1_gemini", "rate_limited", tier1_error))
                # Continue to try anyway or fail, since fallback is disabled
            else:
                logger.debug("Gemini rate limit token acquired")

        if not tier1_error:
            try:
                tier1_response = _call_gemini(
                    model=current_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    api_key=config.gemini_api_key
                )
                attempts.append(("tier1_gemini", "success", len(tier1_response)))

                # success!
                return tier1_response

            except Exception as e:
                tier1_error = str(e)
                attempts.append(("tier1_gemini", "failed", tier1_error))
                logger.error(f"LLM Tier 1 (Gemini) failed: {tier1_error}")
                
                # If Pro failed, maybe try Flash as backup?
                if force_tier2:
                     logger.warning("Gemini Pro failed, falling back to Flash for robustness")
                     return chat_complete(messages, max_tokens=max_tokens, temperature=temperature, timeout=timeout, force_tier2=False)
                
                raise LLMError(f"Gemini failed. Error: {tier1_error}")
    else:
        logger.error("GEMINI_API_KEY not configured!")
        raise LLMError("GEMINI_API_KEY not configured")
    
    raise LLMError(f"LLM Processing Failed. Attempts: {attempts}")


# -----------------------------------------------------------------------------
# Legacy compatibility functions
# -----------------------------------------------------------------------------
def chat_complete_legacy(
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    *,
    max_tokens: int = 1000,
    temperature: float = 0.1,
    timeout: float = 30.0,
    retries: int = 2
) -> str:
    """
    Legacy function for backward compatibility.
    Maps everything to Gemini now.
    """
    # Force everything to use the main chat_complete which enforces Gemini
    return chat_complete(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        force_tier2=False # Always false
    )
