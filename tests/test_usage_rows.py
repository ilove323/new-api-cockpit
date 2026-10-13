"""Request-time price/ratio rows preserve recorded usage across every period."""

import json
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from openpyxl import load_workbook

from session_fixture import fixture_identity, session_auth
from new_api_cockpit.app import app
from new_api_cockpit.metadata_fallback import aggregate
from new_api_cockpit.report import (
    decorate,
    export_excel,
    merge_failures,
    period,
    rankings,
    totals,
)


def record(index, ratio, *, model="gpt", group="GPT", token=10, date="2026-10-01"):
    return dict(
        id=index,
        created_at=period(date, date)[0] + index,
        user_id=1,
        username="fixture",
        token_id=token,
        token_name="key-" + str(token),
        model_name=model,
        group_name=group,
        quota=100,
        prompt_tokens=10 if model.startswith("claude") else 110,
        completion_tokens=1,
        other=json.dumps(
            dict(
                group_ratio=ratio,
                cache_tokens=100,
                model_price=-1,
                model_ratio=1,
                completion_ratio=5,
                cache_ratio=0.1,
            )
        ),
    )


def options(model="gpt"):
    return {
        "ModelRatio": {model: 1},
        "CompletionRatio": {model: 5},
        "CacheRatio": {model: 0.1},
        "GroupRatio": {"GPT": 9, "VIP": 0.5},
    }


class UsageRowsTest(unittest.TestCase):
    def test_price_ratio_rows_preserve_log_counters_for_both_model_conventions(self):
        for model in ("gpt", "claude-test"):
            with self.subTest(model=model):
                rows = decorate(
                    aggregate(
                        [
                            record(1, "3", model=model),
                            record(2, "3.2", model=model),
                            record(3, "3.4", model=model),
                        ],
                        False,
                    ),
                    options(model),
                )
                self.assertEqual(
                    [r["group_ratio"] for r in rows],
                    [Decimal("3"), Decimal("3.2"), Decimal("3.4")],
                )
                for row in rows:
                    self.assertEqual(row["request_count"], 1)
                    self.assertEqual(row["input_tokens"], 10)
                    self.assertEqual(row["output_tokens"], 1)
                    self.assertEqual(row["cache_read_tokens"], 100)
                    self.assertEqual(row["total_tokens"], 111)
                    self.assertEqual(row["amount"], Decimal("0.0002"))
                    formula = row["cost_formula"]
                    self.assertEqual(
                        set(formula),
                        {
                            "mode",
                            "prices",
                            "buckets",
                            "calculated",
                            "difference",
                            "matched_tier",
                        },
                    )
                    self.assertEqual(formula["buckets"][0]["ratio"], row["group_ratio"])
                    self.assertEqual(formula["buckets"][0]["terms"][2]["tokens"], 100)
                summary = totals(rows)
                self.assertEqual(summary["request_count"], 3)
                self.assertEqual(summary["total_tokens"], 333)
                self.assertEqual(summary["cache_read_tokens"], 300)
                self.assertEqual(summary["amount"], Decimal("0.0006"))
                self.assertEqual(rankings(rows)["user_tokens"][0]["total_tokens"], 333)

    def test_dates_and_current_group_configuration_do_not_change_usage_rows(self):
        records = [
            record(1, "3", date="2026-09-30"),
            record(2, "3.0", group="VIP", date="2026-10-01"),
            record(3, "4", date="2027-01-01"),
        ]
        source = aggregate(records, False)
        configuration = options()
        baseline = decorate(deepcopy(source), configuration)
        configuration["GroupRatio"] = {"GPT": 0.1, "VIP": 99}
        self.assertEqual(decorate(deepcopy(source), configuration), baseline)
        self.assertEqual([r["group_ratio"] for r in baseline], [Decimal(3), Decimal(4)])
        self.assertEqual([r["request_count"] for r in baseline], [2, 1])
        self.assertEqual([r["total_tokens"] for r in baseline], [222, 111])
        for date in ("2026-09-30", "2026-10-01", "2027-01-01"):
            first, last = period(date, date)
            selected = [r for r in records if first <= r["created_at"] < last]
            row = decorate(aggregate(selected, False), configuration)[0]
            self.assertEqual(row["total_tokens"], 111)
            self.assertEqual(row["cache_read_tokens"], 100)

    def test_token_dimensions_and_failure_counts_are_not_duplicated_by_ratio(self):
        records = [
            record(i, ratio, token=token)
            for i, (ratio, token) in enumerate([(3, 10), (4, 10), (3, 20), (4, 20)], 1)
        ]
        failure = dict(record(5, 3), quota=0, other='{"status_code":429}')
        for by_token, expected in [(True, 4), (False, 2)]:
            with self.subTest(by_token=by_token):
                source = aggregate(records, by_token)
                merge_failures(
                    source, aggregate([failure], by_token, failures=True), by_token
                )
                rows = decorate(source, options())
                self.assertEqual(len(rows), expected)
                self.assertEqual(sum(r["request_count"] for r in rows), 4)
                self.assertEqual(sum(r["failure_count"] for r in rows), 1)
                self.assertEqual(sum(sum(r["failure_codes"].values()) for r in rows), 1)
                self.assertEqual(totals(rows)["amount"], Decimal("0.0008"))
                self.assertEqual(totals(rows)["total_tokens"], 444)
                self.assertEqual(
                    {r["token_id"] for r in rows}, {10, 20} if by_token else {0}
                )

    def test_zero_charges_and_missing_prices_do_not_change_usage(self):
        free = dict(record(1, 3), quota=0)
        unknown = dict(record(2, 4), other='{"group_ratio":4,"cache_tokens":100}')
        rows = decorate(aggregate([free, unknown], False), options())
        self.assertEqual(totals(rows)["total_tokens"], 222)
        self.assertEqual(totals(rows)["cache_read_tokens"], 200)
        self.assertEqual(totals(rows)["amount"], Decimal("0.0002"))
        self.assertEqual(sum(r["amount"] == 0 for r in rows), 1)
        self.assertEqual(sum(r["input_price"] is None for r in rows), 1)

    def test_excel_and_api_share_logged_counts_and_ratios(self):
        rows = decorate(aggregate([record(1, 3), record(2, 4)], False), options())
        start, end = "2026-09-30", "2026-10-01"
        summary = totals(rows, start, end)
        self.assertEqual(summary["total_tokens"], 222)
        self.assertEqual(summary["tpm"], Decimal(222) * 60 / 172800)
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.app.load_report", return_value=rows),
        ):
            response = app.test_client().get(
                "/cockpit/api/statistics/usage?start=" + start + "&end=" + end,
                auth=session_auth("fixture-admin"),
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["totals"]["total_tokens"], 222)
        self.assertEqual(body["rankings"]["user_tokens"][0]["total_tokens"], 222)
        self.assertEqual(
            [Decimal(r["group_ratio"]) for r in body["rows"]], [Decimal(3), Decimal(4)]
        )
        self.assertEqual([r["cache_read_tokens"] for r in body["rows"]], [100, 100])
        workbook = load_workbook(export_excel(rows, start, end))
        sheet = workbook["用户模型用量"]
        for line, ratio in [(3, 3), (4, 4)]:
            self.assertEqual(sheet[f"F{line}"].value, 111)
            self.assertEqual(sheet[f"I{line}"].value, 100)
            self.assertEqual(sheet[f"K{line}"].value, ratio)
            self.assertEqual(sheet[f"K{line}"].data_type, "n")
        self.assertEqual(sheet["F5"].value, "=SUM(F3:F4)")


if __name__ == "__main__":
    unittest.main()
