"""Recover request-time token prices from New API consumption-log snapshots.

No administrator expression is evaluated. Unsupported or incomplete snapshots
remain unknown rather than borrowing today's tariff.
"""

import base64
import binascii
from decimal import Decimal, InvalidOperation

from .expression_prices import extract_prices


PRICE_FIELDS = ("input_price", "output_price", "cache_price", "write_price")
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def number(value):
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def request_prices(snapshot):
    """Return four historical unit prices, or None when not recoverable."""
    snapshot = snapshot or {}
    encoded = snapshot.get("expr_b64")
    if encoded:
        try:
            expression = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeError, ValueError):
            return None
        tiers = extract_prices(expression)
        if not tiers:
            return None
        matched = snapshot.get("matched_tier") or ""
        tier = next((item for item in tiers if item["name"] == matched), None)
        if tier is None and len(tiers) == 1 and not matched:
            tier = tiers[0]
        return {field: tier[field] for field in PRICE_FIELDS} if tier else None

    fixed = number(snapshot.get("model_price"))
    if fixed is not None and fixed >= 0:
        return None  # Per-request billing has no four token unit prices.
    model = number(snapshot.get("model_ratio"))
    completion = number(snapshot.get("completion_ratio"))
    cache = number(snapshot.get("cache_ratio"))
    if model is None or model <= 0 or completion is None or cache is None:
        return None
    base = model * 2
    write = number(snapshot.get("cache_creation_ratio_5m"))
    if write is None:
        write = number(snapshot.get("cache_creation_ratio"))
    return {
        "input_price": base,
        "output_price": base * completion,
        "cache_price": base * cache,
        "write_price": base * write if write is not None else None,
    }


def price_key(prices):
    return tuple(prices[field] for field in PRICE_FIELDS) if prices else None


def matching_current_price(prices, candidates, usage):
    """Match only unique tariffs; unused cache categories may be unspecified."""
    if (
        prices is None
        or prices["input_price"] is None
        or prices["output_price"] is None
    ):
        return None
    matches = []
    for candidate in candidates:
        if any(prices[field] != candidate.get(field) for field in PRICE_FIELDS[:2]):
            continue
        for field, token in zip(PRICE_FIELDS[2:], USAGE_FIELDS[2:]):
            historical, current = prices[field], candidate.get(field)
            if historical != current and (
                int(usage.get(token, 0)) > 0
                or (historical is not None and current is not None)
            ):
                break
        else:
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None
