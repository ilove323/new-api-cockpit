"""Read-only extraction of simple New API token-price expressions.

Never execute an administrator-supplied expression. Unknown syntax is left
unpriced rather than silently treating a partial parse as the full tariff.
"""

import re
from decimal import Decimal


PRICE_VARS = {
    "p": "input_price",
    "c": "output_price",
    "cr": "cache_price",
    "cc": "write_price",
    "cc1h": "write_1h_price",
}
NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)"
TERM = re.compile(rf"(p|c|cr|cc|cc1h)\s*\*\s*({NUMBER})\Z")
TIER = r'tier\(\s*"([^"\\]+)"\s*,\s*([^()]*)\)'
SINGLE = re.compile(rf"\s*{TIER}\s*\Z")
SPLIT = re.compile(rf"\s*len\s*(<=|<|>=|>)\s*(\d+)\s*\?\s*{TIER}\s*:\s*{TIER}\s*\Z")


def _tier(name, body, condition=None):
    prices = dict.fromkeys(PRICE_VARS.values())
    for term in body.split("+"):
        match = TERM.fullmatch(term.strip())
        if not match:
            return None
        field = PRICE_VARS[match.group(1)]
        if prices[field] is not None:
            return None
        prices[field] = Decimal(match.group(2))
    return {"name": name, "condition": condition, **prices}


def extract_prices(expression):
    """Return complete token-only tiers, or None for unsupported expressions."""
    if not isinstance(expression, str):
        return None
    expr = expression.strip()
    if expr.startswith("v1:"):
        expr = expr[3:].strip()
    match = SINGLE.fullmatch(expr)
    if match:
        tier = _tier(match.group(1), match.group(2))
        return [tier] if tier else None
    match = SPLIT.fullmatch(expr)
    if not match:
        return None
    op, limit, first_name, first_body, second_name, second_body = match.groups()
    first = _tier(first_name, first_body, f"len {op} {limit}")
    second = _tier(
        second_name,
        second_body,
        f"len {'>' if op == '<=' else '>=' if op == '<' else '<=' if op == '>' else '<'} {limit}",
    )
    return [first, second] if first and second else None


def low_tier(tiers):
    """Prefer the short-context branch; otherwise choose the lowest input price."""
    for tier in tiers:
        if tier.get("condition") and re.fullmatch(
            r"len\s*<(?:=)?\s*\d+", tier["condition"]
        ):
            return tier
    infinity = Decimal("Infinity")
    return min(
        tiers,
        key=lambda tier: (
            tier["input_price"] if tier["input_price"] is not None else infinity,
            tier["output_price"] if tier["output_price"] is not None else infinity,
        ),
    )
