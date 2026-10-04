"""Safe channel errors may be displayed; arbitrary upstream response text may not."""


class DeliveryError(Exception):
    """Sanitized delivery failure without credentials or raw response bodies."""
