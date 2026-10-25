"""Safe Retry-After projection; no client cooldown or retry policy lives here."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


def retry_after(value, *, now=None):
    """Return bounded header syntax as delay seconds, never arbitrary upstream text."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    value = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    if value.isascii() and value.isdigit():
        return str(int(value)) if len(value) <= 10 else None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        current = now or datetime.now(timezone.utc)
        return str(max(0, math.ceil((date - current).total_seconds())))
    except (ValueError, TypeError, OverflowError):
        return None
