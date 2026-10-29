"""Presentation-only USD/RMB conversion; never reprice or write source charges."""

from decimal import Decimal, InvalidOperation

# New API v1.0.0-rc.47 setting/operation_setting/payment_setting_old.go.
# Persisted USDExchangeRate always wins; an invalid saved value is not replaced.
NEW_API_DEFAULT_USD_CNY_RATE = Decimal("7.3")


def rate_value(raw):
    try:
        rate = Decimal(str(raw))
        if rate.is_finite() and 0 < rate <= 1000000 and float(rate) > 0:
            return rate
    except (InvalidOperation, ValueError):
        pass
    return None


def settings(options):
    supplied = "USDExchangeRate" in options
    raw = options.get("USDExchangeRate", NEW_API_DEFAULT_USD_CNY_RATE)
    rate = rate_value(raw)
    return {
        "base_currency": "USD",
        "default_currency": "CNY",
        "usd_cny_rate": rate,
        "rate_source": "USDExchangeRate" if supplied else "new_api_default",
    }


def rmb_rows(rows, rate):
    """Copy only exported money fields; tokens, ratios and original DTOs survive."""
    rate = rate_value(rate)
    if rate is None:
        raise ValueError(
            "站点 USDExchangeRate 无效，无法导出 RMB，请先修正汇率。"
        ) from None

    def convert(value):
        return Decimal(str(value)) * rate if value is not None else None

    prices = ("input_price", "output_price", "cache_price", "write_price")
    result = []
    for row in rows:
        copied = dict(row)
        for key in (*prices, "amount"):
            if key in row:
                copied[key] = convert(row[key])
        if "price_tiers" in row:
            copied["price_tiers"] = [
                dict(tier, **{key: convert(tier.get(key)) for key in prices})
                for tier in row["price_tiers"] or []
            ]
        result.append(copied)
    return result
