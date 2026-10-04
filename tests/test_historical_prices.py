import base64
import unittest
from decimal import Decimal

from new_api_cockpit.historical_prices import matching_current_price, request_prices
from new_api_cockpit.report import decorate, totals


class HistoricalPricesTest(unittest.TestCase):
    def test_request_snapshot_prices(self):
        legacy = request_prices(
            {
                "model_price": "-1",
                "model_ratio": "1.25",
                "completion_ratio": "6",
                "cache_ratio": "0.1",
                "cache_creation_ratio_5m": "1.25",
            }
        )
        self.assertEqual(
            legacy,
            dict(
                input_price=Decimal("2.50"),
                output_price=Decimal("15.00"),
                cache_price=Decimal("0.250"),
                write_price=Decimal("3.1250"),
            ),
        )
        expression = 'len <= 100 ? tier("old", p * 2 + cr * 0.2 + c * 10) : tier("high", p * 4 + cr * 0.4 + c * 20)'
        snapshot = dict(
            expr_b64=base64.b64encode(expression.encode()).decode(),
            matched_tier="old",
            model_ratio="0",
        )
        self.assertEqual(request_prices(snapshot)["cache_price"], Decimal("0.2"))
        self.assertIsNone(request_prices({**snapshot, "matched_tier": "missing"}))
        self.assertIsNone(request_prices({"expr_b64": "not-base64"}))
        self.assertIsNone(request_prices({"model_price": "1", "model_ratio": "1"}))

    def test_match_uses_consumed_prices_not_old_tier_name(self):
        old = request_prices(
            dict(
                model_price="-1",
                model_ratio="1",
                completion_ratio="5",
                cache_ratio="0.1",
            )
        )
        current = dict(
            name="low",
            input_price=Decimal(2),
            output_price=Decimal(10),
            cache_price=Decimal("0.2"),
            write_price=Decimal("2.5"),
        )
        self.assertEqual(
            matching_current_price(
                old, [current], {"cache_read_tokens": 100, "cache_write_tokens": 0}
            ),
            current,
        )
        self.assertIsNone(
            matching_current_price(
                old, [current], {"cache_read_tokens": 100, "cache_write_tokens": 1}
            )
        )

    def test_historical_price_rows_and_changed_ratio_reconciliation(self):
        expression = 'len <= 1000 ? tier("low", p * 2 + cr * 0.2 + cc * 2.5 + c * 10) : tier("high", p * 4 + cr * 0.4 + cc * 5 + c * 20)'
        current = {
            "billing_setting.billing_mode": {"m": "tiered_expr"},
            "billing_setting.billing_expr": {"m": expression},
            "GroupRatio": {"GPT": 3.4},
        }

        def bucket(snapshot, ratio, amount, latest):
            return dict(
                matched_tier=snapshot.get("matched_tier") or "",
                price_snapshot=snapshot,
                group_name="GPT",
                group_ratio=str(ratio),
                request_count=1,
                raw_input_tokens=110,
                pricing_input_tokens=10,
                input_tokens=10,
                output_tokens=1,
                cache_read_tokens=100,
                cache_write_tokens=0,
                total_tokens=111,
                amount=str(amount),
                latest_at=latest,
                latest_id=latest,
            )

        old_snapshot = dict(
            model_price="-1", model_ratio="1", completion_ratio="5", cache_ratio="0.1"
        )
        now_snapshot = dict(
            expr_b64=base64.b64encode(expression.encode()).decode(), matched_tier="low"
        )
        old_price = dict(
            model_price="-1", model_ratio="1.5", completion_ratio="5", cache_ratio="0.1"
        )
        source = dict(
            user_id=1,
            username="tester",
            display_name="",
            model_name="m",
            token_id=0,
            token_name="",
            request_count=3,
            raw_input_tokens=330,
            pricing_input_tokens=30,
            input_tokens=30,
            output_tokens=3,
            cache_read_tokens=300,
            cache_write_tokens=0,
            total_tokens=333,
            ratio_count=2,
            group_ratio=Decimal("3.4"),
            amount=Decimal("0.000575"),
            failure_codes={},
            failure_count=0,
            tier_usage=[
                bucket(old_snapshot, 3, "0.00015", 1),
                bucket(now_snapshot, 3.4, "0.00017", 2),
                bucket(old_price, 3.4, "0.000255", 3),
            ],
        )
        rows = decorate([source], current)
        self.assertEqual(len(rows), 2)
        low, historical = rows
        self.assertEqual(low["tier_name"], "low")
        self.assertEqual(low["request_count"], 2)
        self.assertEqual(low["amount"], Decimal("0.00032"))
        self.assertEqual(low["original_cache_read_tokens"], 200)
        self.assertEqual(low["cache_read_tokens"], 171)
        self.assertEqual(low["total_tokens"], 193)
        self.assertEqual(low["group_ratio"], Decimal("3.4"))
        self.assertTrue(low["cost_formula"]["converted"])
        self.assertEqual(low["cost_formula"]["calculated"], Decimal("0.00032028"))
        self.assertEqual(historical["tier_name"], "-")
        self.assertEqual(historical["input_price"], Decimal(3))
        self.assertEqual(historical["cache_read_tokens"], 100)
        self.assertEqual(historical["cost_formula"]["calculated"], Decimal("0.000255"))
        self.assertEqual(totals(rows)["amount"], source["amount"])
        self.assertEqual(sum(row["request_count"] for row in rows), 3)

    def test_three_historical_ratios_merge_when_price_matches(self):
        options = {
            "ModelRatio": {"m": 1},
            "CompletionRatio": {"m": 5},
            "CacheRatio": {"m": 0.1},
            "GroupRatio": {"GPT": 3.4},
        }
        snapshot = dict(
            model_price="-1", model_ratio="1", completion_ratio="5", cache_ratio="0.1"
        )
        buckets = []
        for index, ratio in enumerate((3, 3.2, 3.4), 1):
            buckets.append(
                dict(
                    price_snapshot=snapshot,
                    group_name="GPT",
                    group_ratio=str(ratio),
                    request_count=1,
                    raw_input_tokens=110,
                    pricing_input_tokens=10,
                    input_tokens=10,
                    output_tokens=1,
                    cache_read_tokens=100,
                    cache_write_tokens=0,
                    total_tokens=111,
                    amount=str(Decimal("0.00005") * Decimal(str(ratio))),
                    latest_at=index,
                    latest_id=index,
                )
            )
        source = dict(
            user_id=1,
            username="u",
            display_name="",
            model_name="m",
            request_count=3,
            raw_input_tokens=330,
            pricing_input_tokens=30,
            input_tokens=30,
            output_tokens=3,
            cache_read_tokens=300,
            cache_write_tokens=0,
            total_tokens=333,
            amount=Decimal("0.00048"),
            group_ratio=Decimal("3.4"),
            tier_usage=buckets,
        )
        rows = decorate([source], options)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_count"], 3)
        self.assertEqual(rows[0]["original_cache_read_tokens"], 300)
        self.assertEqual(rows[0]["cache_read_tokens"], 256)
        self.assertEqual(rows[0]["total_tokens"], 289)
        self.assertEqual(rows[0]["amount"], Decimal("0.00048"))

    def test_unpriced_request_does_not_borrow_current_tariff(self):
        source = dict(
            user_id=1,
            username="u",
            display_name="",
            model_name="m",
            request_count=1,
            raw_input_tokens=1,
            pricing_input_tokens=1,
            input_tokens=1,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            total_tokens=1,
            amount=Decimal("1"),
            group_ratio=Decimal(1),
            tier_usage=[
                dict(
                    price_snapshot={"model_price": "1"},
                    group_name="GPT",
                    group_ratio="1",
                    request_count=1,
                    raw_input_tokens=1,
                    pricing_input_tokens=1,
                    input_tokens=1,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    total_tokens=1,
                    amount="1",
                    latest_at=1,
                    latest_id=1,
                )
            ],
        )
        rows = decorate(
            [source],
            {
                "ModelRatio": {"m": 1},
                "CompletionRatio": {"m": 5},
                "CacheRatio": {"m": 0.1},
                "GroupRatio": {"GPT": 3.4},
            },
        )
        self.assertIsNone(rows[0]["input_price"])
        self.assertEqual(rows[0]["tier_name"], "-")
        self.assertIsNone(rows[0]["cost_formula"]["calculated"])


if __name__ == "__main__":
    unittest.main()
