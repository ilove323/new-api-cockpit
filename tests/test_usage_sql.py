"""Run with PGHOST configured: python -m unittest discover -s tests.

Read-only PostgreSQL integration test; a CTE shadows logs with synthetic rows.
"""

import json
import os
import unittest
import psycopg
from psycopg.rows import dict_row
from new_api_cockpit.report import FAILURE_SQL, SQL


@unittest.skipUnless(os.environ.get("PGHOST"), "PostgreSQL integration requires PGHOST")
class UsageSQLTest(unittest.TestCase):
    def test_failure_status_aggregation_respects_token_and_group(self):
        fixtures = [
            dict(
                id=index,
                created_at=index,
                user_id=1,
                username="fixture",
                token_id=token_id,
                token_name=token_name,
                model_name="gpt",
                other=json.dumps({"status_code": code}),
                type=kind,
                group=group,
            )
            for index, token_id, token_name, code, kind, group in [
                (1, 10, "old-name", "429", 5, "auto"),
                (2, 10, "new-name", "429", 5, "auto"),
                (3, 10, "new-name", "502", 5, "auto"),
                (4, 20, "other-key", "503", 5, "vip"),
                (5, 10, "new-name", "500", 2, "auto"),
            ]
        ]
        prefix = """WITH logs AS (
            SELECT * FROM jsonb_to_recordset(%(fixtures)s::jsonb) AS r(
                id bigint, created_at bigint, user_id bigint, username text,
                token_id bigint, token_name text, model_name text, other text,
                type int, "group" text)
        ), errors AS MATERIALIZED ("""
        query = FAILURE_SQL.replace("WITH errors AS MATERIALIZED (", prefix, 1)
        with psycopg.connect(
            connect_timeout=8,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
        ) as conn:
            rows = conn.execute(
                query,
                dict(
                    fixtures=json.dumps(fixtures),
                    start=0,
                    end=10,
                    by_token=True,
                    token_ids=[10],
                    groups=["auto"],
                ),
            ).fetchall()
        self.assertEqual(
            [(row["status_code"], row["failure_count"]) for row in rows],
            [("429", 2), ("502", 1)],
        )
        self.assertEqual({row["token_id"] for row in rows}, {10})
        self.assertEqual({row["token_name"] for row in rows}, {"new-name"})

    def test_site_model_convention_and_latest_ratio(self):
        cases = [
            ("gpt", None, 100, 20, 30, 10, 60, 120),
            ("claude-native", "anthropic", 100, 20, 30, 10, 100, 160),
            ("claude-converted", "openai", 100, 20, 30, 10, 100, 160),
            ("alias", "anthropic", 100, 20, 30, 10, 60, 120),
            ("glm", None, 100, 20, 30, 10, 60, 120),
            ("deepseek", None, 100, 20, 30, 10, 60, 120),
            ("kimi", None, 100, 20, 30, 10, 60, 120),
            ("gpt-overlap", "openai", 100, 20, 80, 40, 0, 120),
        ]
        fixtures = []
        for model, semantic, p, c, cr, cw, _, _ in cases:
            for index, ratio in enumerate((3, 3, 3.4), 1):
                other = dict(cache_tokens=cr, cache_write_tokens=cw, group_ratio=ratio)
                if semantic:
                    other["usage_semantic"] = semantic
                fixtures.append(
                    dict(
                        id=index,
                        created_at=index,
                        user_id=1,
                        username="fixture",
                        token_id=10,
                        token_name="fixture-key",
                        model_name=model,
                        quota=100,
                        prompt_tokens=p,
                        completion_tokens=c,
                        other=json.dumps(other),
                        type=2,
                        group="auto",
                    )
                )
        prefix = """WITH logs AS (
            SELECT * FROM jsonb_to_recordset(%(fixtures)s::jsonb) AS r(
                id bigint, created_at bigint, user_id bigint, username text,
                token_id bigint, token_name text, model_name text,
                quota bigint, prompt_tokens bigint, completion_tokens bigint, other text,
                type int, "group" text)
        ), source AS MATERIALIZED ("""
        query = SQL.replace("WITH source AS MATERIALIZED (", prefix, 1)
        with psycopg.connect(
            connect_timeout=8,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
        ) as conn:
            rows = conn.execute(
                query,
                dict(
                    fixtures=json.dumps(fixtures),
                    start=0,
                    end=10,
                    by_token=False,
                    token_ids=None,
                    groups=None,
                ),
            ).fetchall()
        by_model = {row["model_name"]: row for row in rows}
        for model, _, p, c, cr, cw, clean, total in cases:
            row = by_model[model]
            self.assertEqual(row["input_tokens"], clean * 3)
            self.assertEqual(row["pricing_input_tokens"], clean * 3)
            self.assertEqual(row["raw_input_tokens"], p * 3)
            self.assertEqual(row["output_tokens"], c * 3)
            self.assertEqual(row["total_tokens"], total * 3)
            self.assertEqual(float(row["group_ratio"]), 3.4)
            self.assertEqual(row["ratio_count"], 2)

    def test_optional_token_dimension_keeps_same_names_separate(self):
        fixtures = [
            dict(
                id=token_id,
                created_at=token_id,
                user_id=1,
                username="fixture",
                token_id=token_id,
                token_name="same-name",
                model_name="gpt",
                quota=500000,
                prompt_tokens=100 * token_id,
                completion_tokens=0,
                other="{}",
                type=2,
                group="auto",
            )
            for token_id in (1, 2)
        ]
        prefix = """WITH logs AS (
            SELECT * FROM jsonb_to_recordset(%(fixtures)s::jsonb) AS r(
                id bigint, created_at bigint, user_id bigint, username text,
                token_id bigint, token_name text, model_name text,
                quota bigint, prompt_tokens bigint, completion_tokens bigint, other text,
                type int, "group" text)
        ), source AS MATERIALIZED ("""
        query = SQL.replace("WITH source AS MATERIALIZED (", prefix, 1)
        with psycopg.connect(
            connect_timeout=8,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
        ) as conn:
            split = conn.execute(
                query,
                dict(
                    fixtures=json.dumps(fixtures),
                    start=0,
                    end=10,
                    by_token=True,
                    token_ids=None,
                    groups=None,
                ),
            ).fetchall()
            merged = conn.execute(
                query,
                dict(
                    fixtures=json.dumps(fixtures),
                    start=0,
                    end=10,
                    by_token=False,
                    token_ids=None,
                    groups=None,
                ),
            ).fetchall()
        self.assertEqual([row["token_id"] for row in split], [1, 2])
        self.assertEqual([row["token_name"] for row in split], ["same-name"] * 2)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["token_id"], 0)
        self.assertEqual(merged[0]["total_tokens"], 300)


if __name__ == "__main__":
    unittest.main()
