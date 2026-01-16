# app/services/sms_alerts.py
# SMS alert system for LLM failures and critical errors
# Optional: Requires Twilio configuration

import os
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Throttle alerts to prevent spam (max 1 per hour)
_last_alert_time = 0
_alert_throttle_seconds = 3600

def send_llm_failure_alert(message: str) -> bool:
    """
    Send SMS alert for LLM failures.
    Throttled to prevent spam (max 1 per hour).

    Args:
        message: Alert message to send

    Returns:
        True if alert was sent, False if throttled or disabled
    """
    global _last_alert_time

    # Check if alerts are enabled
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_from = os.getenv("TWILIO_FROM_NUMBER")
    alert_to = os.getenv("ALERT_PHONE_NUMBER")

    if not all([twilio_sid, twilio_token, twilio_from, alert_to]):
        logger.debug("SMS alerts not configured, skipping alert")
        return False

    # Check throttle
    current_time = time.time()
    if current_time - _last_alert_time < _alert_throttle_seconds:
        logger.debug("SMS alert throttled (too soon since last alert)")
        return False

    try:
        # Try to import Twilio
        from twilio.rest import Client

        # Create client
        client = Client(twilio_sid, twilio_token)

        # Send message
        client.messages.create(
            body=message,
            from_=twilio_from,
            to=alert_to
        )

        # Update throttle time
        _last_alert_time = current_time
        logger.info(f"SMS alert sent: {message[:50]}...")
        return True

    except ImportError:
        logger.warning("Twilio not installed. Run: pip install twilio")
        return False

    except Exception as e:
        logger.error(f"Failed to send SMS alert: {str(e)}")
        return False


def send_critical_alert(message: str, override_throttle: bool = False) -> bool:
    """
    Send critical alert (can override throttle).

    Args:
        message: Alert message
        override_throttle: If True, bypass throttle limit

    Returns:
        True if sent, False otherwise
    """
    global _last_alert_time

    if override_throttle:
        _last_alert_time = 0  # Reset throttle

    return send_llm_failure_alert(message)


def configure_alert_throttle(seconds: int) -> None:
    """
    Configure alert throttle time.

    Args:
        seconds: Minimum seconds between alerts
    """
    global _alert_throttle_seconds
    _alert_throttle_seconds = seconds
    logger.info(f"Alert throttle set to {seconds} seconds")
