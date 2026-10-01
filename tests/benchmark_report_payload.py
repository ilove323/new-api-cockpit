"""Synthetic payload comparison; MONITOR_DATABASE_URL must be disposable PG.

No upstream requests or production logs. Run separately, not as a unit test.
"""

import json
import os
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from new_api_statistics import balance, report, report_snapshots, metadata_fallback


def main():
    dsn = os.environ["MONITOR_DATABASE_URL"]
    schema = "payload_benchmark_" + uuid.uuid4().hex
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect():
        return psycopg.Connection.connect(
            dsn, row_factory=dict_row, options="-c search_path=" + schema
        )

    try:
        records = []
        for user_id in range(1, 201):
            for index in range(20):
                ratio = Decimal(3) + Decimal(index) / 100
                records.append(
                    dict(
                        id=user_id * 100 + index,
                        created_at=index + 1,
                        user_id=user_id,
                        username=f"fixture-{user_id}",
                        token_id=user_id,
                        token_name="fixture-key",
                        model_name="gpt-fixture",
                        group_name="auto",
                        prompt_tokens=100,
                        completion_tokens=20,
                        quota=int(
                            (Decimal(351) * ratio / 2).quantize(
                                Decimal(1), rounding=ROUND_HALF_UP
                            )
                        ),
                        other=json.dumps(
                            dict(
                                cache_tokens=30,
                                cache_write_tokens=10,
                                group_ratio=str(ratio),
                                model_ratio=1,
                                completion_ratio=5,
                                cache_ratio="0.1",
                                cache_creation_ratio="1.25",
                            )
                        ),
                    )
                )
        rows = report.decorate(metadata_fallback.aggregate(records, False), {})
        full_size = len(report_snapshots.dumps(rows).encode())
        with (
            patch.object(balance, "connect", connect),
            patch.object(balance, "configured", return_value=True),
        ):
            balance.initialize()
            begin = time.perf_counter()
            slim = report_snapshots.create(rows, "fixture-owner", 1)
            write_ms = (time.perf_counter() - begin) * 1000
            slim_size = len(report_snapshots.dumps(slim).encode())
            begin = time.perf_counter()
            report_snapshots.fetch(
                dict(report_id=slim[0]["report_id"], row_ids=[0]), "fixture-owner", 1
            )
            detail_ms = (time.perf_counter() - begin) * 1000
        print(
            json.dumps(
                dict(
                    logs=len(records),
                    rows=len(rows),
                    full_bytes=full_size,
                    slim_bytes=slim_size,
                    reduction_percent=round((1 - slim_size / full_size) * 100, 2),
                    snapshot_write_ms=round(write_ms, 2),
                    detail_read_ms=round(detail_ms, 2),
                )
            )
        )
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


if __name__ == "__main__":
    main()
