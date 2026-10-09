"""Channel quality regression: synthetic source logs and no production writes."""

import json
import os
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from werkzeug.datastructures import MultiDict

from database import isolated_schema
from session_fixture import fixture_identity, session_auth
from new_api_cockpit import quality
from new_api_cockpit.app import app, safe_next


def event(attempt, channel, elapsed, action, **extra):
    return {
        "attempt": attempt,
        "channel_id": channel,
        "group": "default",
        "elapsed_ms": elapsed,
        "decision": {"action": action, **extra.pop("decision", {})},
        **extra,
    }


def metadata(events=(), stream=None, **extra):
    return {
        "request_path": "/v1/chat/completions",
        "frt": 300,
        "admin_info": {"request_policy": list(events), "use_channel": ["1"]},
        "model_ratio": 1,
        "completion_ratio": 5,
        "cache_ratio": 0.1,
        "model_price": 0,
        "cache_creation_ratio_5m": 1.25,
        **({"stream_status": stream} if stream is not None else {}),
        **extra,
    }


class QualityContractTest(unittest.TestCase):
    def test_enabled_catalog_skips_inventory_and_history_keeps_live_identity(self):
        live = dict(
            channel_id=1,
            channel_name="current",
            channel_status=1,
            tag_value="current-tag",
            is_deleted=False,
        )
        stored = [
            {**live, "channel_name": "previous", "tag_value": "old-tag"},
            {
                **live,
                "channel_id": 2,
                "channel_name": "deleted",
                "channel_status": None,
                "is_deleted": True,
            },
        ]
        source, monitor = MagicMock(), MagicMock()
        source.execute.return_value = [live]
        monitor.__enter__.return_value = monitor
        monitor.execute.return_value = stored
        with (
            patch.object(quality.balance, "configured", return_value=True),
            patch.object(quality.balance, "require_schema") as require_schema,
            patch.object(quality.balance, "connect", return_value=monitor) as connect,
        ):
            self.assertEqual(quality.catalog(source), [live])
            self.assertIn("WHERE status=1", source.execute.call_args.args[0])
            connect.assert_not_called()
            require_schema.assert_not_called()
            self.assertEqual(
                quality.catalog(source, include_history=True), [live, stored[1]]
            )
            self.assertNotIn("WHERE status=1", source.execute.call_args.args[0])
            connect.assert_called_once()
            require_schema.assert_called_once()

    def test_validation_and_login_return_path(self):
        for args in (
            {"mode": "wrong"},
            {"stream": "bad"},
            {"channel_status": "disabled"},
            {"channel_id": "-1"},
            {"scope_id": "2"},
            {"group": "default"},
            {"tier": "base"},
            {"start": "2026-01-01", "end": "2026-02-03"},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                quality.parameters(MultiDict(args))
        self.assertEqual(quality.parameters(MultiDict())["channel_status"], "enabled")
        self.assertEqual(safe_next("/cockpit/quality/"), "/cockpit/quality/")
        self.assertEqual(
            safe_next("/cockpit/api/quality/export?start=2026-10-01"),
            "/cockpit/statistics/",
        )
        self.assertFalse(hasattr(quality, "export_excel"))
        self.assertIn(
            "/cockpit/api/statistics/export",
            {rule.rule for rule in app.url_map.iter_rules()},
        )
        with (
            patch("new_api_cockpit.app.request_identity", side_effect=fixture_identity),
            patch("new_api_cockpit.app.load_site_name", return_value="Fixture"),
        ):
            client = app.test_client()
            self.assertEqual(client.get("/cockpit/quality/").status_code, 302)
            self.assertEqual(
                client.get("/cockpit/api/quality/summary").status_code, 401
            )
            response = client.get("/cockpit/quality/", auth=session_auth("admin"))
            self.assertEqual(response.status_code, 200)
            self.assertIn('href="/cockpit/quality/" aria-current="page"', response.text)
            self.assertNotIn("<th>排除 / 未知</th>", response.text)
            self.assertNotIn("<th>状态</th>", response.text)
            self.assertNotIn("<th>上游</th>", response.text)
            self.assertEqual(response.text.count("<th>请求次数</th>"), 1)
            self.assertNotIn('id="quality-detail"', response.text)
            self.assertNotIn("<dialog", response.text)
            self.assertNotIn('id="quality-export"', response.text)
            self.assertNotIn("<th>调用记录数</th>", response.text)
            self.assertIn("<th>失败 / 错误码</th>", response.text)
            self.assertEqual(response.text.count("<th>首字/总耗时 P50</th>"), 1)
            self.assertEqual(response.text.count("<th>首字/总耗时 P95</th>"), 1)
            self.assertEqual(response.text.count(">平均 token/s</th>"), 1)
            for text in ("性能健康", "平均延迟", "吞吐量", "流量最高的模型"):
                self.assertIn(text, response.text)
            for text in (
                "统计口径与数据限制",
                'id="quality-warnings"',
                'id="quality-updated"',
                "个模型/渠道组合",
                "单次最多 31 天",
            ):
                self.assertNotIn(text, response.text)
            self.assertNotIn("<th>首响应 P50 / P95</th>", response.text)
            self.assertNotIn("<th>总耗时 P50 / P95</th>", response.text)
            self.assertNotIn('id="scope-tabs"', response.text)
            self.assertNotIn("/cockpit/static/scopes.js", response.text)
            for control in (
                "data-quality-mode",
                'id="quality-stream"',
                'id="quality-latency"',
                'id="quality-clear"',
            ):
                self.assertNotIn(control, response.text)
        with (
            patch(
                "new_api_cockpit.app.verify_pat",
                return_value={"id": 1, "username": "fixture", "role": 100},
            ),
            patch(
                "new_api_cockpit.app.quality.load", return_value={"rows": []}
            ) as query,
            patch(
                "new_api_cockpit.app.scope_context",
                side_effect=AssertionError("Quality must not resolve a billing ledger"),
            ),
        ):
            response = app.test_client().get(
                "/cockpit/api/quality/summary",
                headers={"Authorization": "Bearer fixture-quality-pat"},
            )
            self.assertEqual(response.status_code, 200)
            query.assert_called_once()
            self.assertEqual(query.call_args.kwargs, {})
            query.reset_mock()
            self.assertEqual(
                app.test_client()
                .get(
                    "/cockpit/api/quality/export",
                    headers={"Authorization": "Bearer fixture-quality-pat"},
                )
                .status_code,
                404,
            )
            query.assert_not_called()


@unittest.skipUnless(
    os.environ.get("MONITOR_DATABASE_URL"), "Requires disposable PostgreSQL"
)
class QualitySQLTest(unittest.TestCase):
    def setUp(self):
        self.connect = self.enterContext(isolated_schema())
        self.enterContext(patch.object(quality, "source_connection", self.connect))
        self.enterContext(
            patch.object(quality.balance, "configured", return_value=False)
        )
        self.args = MultiDict({"start": "2026-10-01", "end": "2026-10-01"})
        with self.connect() as conn:
            conn.execute("""CREATE TABLE logs(id bigint,created_at bigint,type int,username text,token_id bigint,
              token_name text,model_name text,channel_id bigint,"group" text,quota bigint,use_time bigint,
              is_stream boolean,request_id text,other text,completion_tokens bigint)""")
            conn.execute(
                "CREATE TABLE channels(id bigint,name text,status int,tag text)"
            )
            conn.execute(
                "INSERT INTO channels VALUES(1,'=upstream',1,'supplier'),(2,'second',2,'supplier'),(3,'third',1,'other')"
            )

    def add(
        self,
        i,
        channel=1,
        kind=2,
        request=None,
        meta=None,
        quota=500000,
        stream=True,
        output_tokens=100,
    ):
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO logs VALUES(%s,%s,%s,'fixture',7,'fixture-key','gpt',%s,'default',%s,3,%s,%s,%s,%s)""",
                (
                    i,
                    1790784000 + i,
                    kind,
                    channel,
                    quota,
                    stream,
                    request or "r" + str(i),
                    meta if isinstance(meta, str) else json.dumps(meta or metadata()),
                    output_tokens,
                ),
            )

    def load(self, **extra):
        # Explicit historical scope for the existing all-channel aggregation fixtures.
        extra.setdefault("channel_status", "all")
        return quality.load(
            MultiDict([*self.args.items(), *extra.items()]), with_trends=True
        )

    def test_default_enabled_scope_applies_before_every_aggregation_without_deleting_history(
        self,
    ):
        with self.connect() as conn:
            conn.execute("UPDATE channels SET status=3 WHERE id=3")
        for i, channel in enumerate((1, 2, 3, 4, 0), 1):
            self.add(
                i,
                channel=channel,
                meta=metadata(
                    [
                        event(1, channel, 0, "attempt"),
                        event(1, channel, i * 1000, "success"),
                    ]
                ),
            )
        # Broken JSON must use the same status rule on the bounded fallback path.
        self.add(6, channel=2, meta="{broken")
        with self.connect() as conn:
            conn.execute(
                "UPDATE logs SET model_name='disabled-only' WHERE channel_id=2"
            )
        result = quality.load(self.args, with_trends=True)
        self.assertEqual(result["channel_status"], "enabled")
        self.assertEqual([r["channel_id"] for r in result["rows"]], [1])
        self.assertEqual([r["channel_id"] for r in result["options"]["channels"]], [1])
        self.assertEqual(result["options"]["models"], ["gpt"])
        totals = result["totals"]
        self.assertEqual(
            (totals["request_count"], totals["success_count"], totals["tps_samples"]),
            (1, 1, 1),
        )
        self.assertEqual(Decimal(totals["amount"]), 1)
        self.assertEqual(totals["duration_p50_ms"], 1000)
        self.assertEqual(totals["duration_p95_ms"], 1000)
        self.assertEqual(totals["avg_tokens_per_second"], 100)
        self.assertEqual(sum(h["request_count"] for h in result["trends"]), 1)
        self.assertEqual(
            sum(b["request_count"] for b in result["rows"][0]["pricing_buckets"]), 1
        )
        empty = self.load(channel_status="enabled", channel_id="2")
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["totals"]["request_count"], 0)
        self.assertEqual(Decimal(empty["totals"]["amount"]), 0)
        historical = self.load()
        self.assertEqual({r["channel_id"] for r in historical["rows"]}, {0, 1, 2, 3, 4})
        self.assertEqual(Decimal(historical["totals"]["amount"]), 6)
        with self.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT SUM(quota) AS quota FROM logs").fetchone()[
                    "quota"
                ],
                3000000,
            )
            conn.execute("UPDATE channels SET status=1 WHERE id=2")
        refreshed = quality.load(self.args)
        self.assertEqual({r["channel_id"] for r in refreshed["rows"]}, {1, 2})
        self.assertEqual(Decimal(refreshed["totals"]["amount"]), 3)

    def test_excluding_disabled_attempt_does_not_make_final_success_a_direct_sample(
        self,
    ):
        first = [
            event(1, 2, 0, "attempt"),
            event(1, 2, 100, "failure", status=429),
            event(1, 2, 100, "retry"),
        ]
        self.add(
            1,
            channel=2,
            kind=5,
            request="retry",
            quota=0,
            meta=metadata(first, status_code=429),
        )
        self.add(
            2,
            channel=1,
            request="retry",
            meta=metadata(
                [*first, event(2, 1, 200, "attempt"), event(2, 1, 1200, "success")]
            ),
        )
        result = quality.load(self.args, with_trends=True)
        self.assertEqual([r["channel_id"] for r in result["rows"]], [1])
        self.assertEqual(
            (
                result["totals"]["request_count"],
                result["totals"]["success_count"],
                result["totals"]["failure_count"],
            ),
            (1, 1, 0),
        )
        self.assertEqual(result["totals"]["failure_codes"], {})
        self.assertEqual(result["totals"]["duration_samples"], 0)
        self.assertEqual(result["totals"]["frt_samples"], 0)
        self.assertEqual(result["totals"]["tps_samples"], 0)
        self.assertIsNone(result["totals"]["avg_duration_ms"])
        self.assertEqual(Decimal(result["totals"]["amount"]), 1)
        full_latency = self.load(channel_status="enabled", latency_scope="all")
        self.assertEqual(full_latency["totals"]["duration_p50_ms"], 1000)
        self.assertEqual(full_latency["totals"]["avg_tokens_per_second"], 100)

    def test_retry_dedup_precise_attempt_latency_and_no_double_charge(self):
        first = [
            event(1, 1, 10, "attempt"),
            event(1, 1, 110, "failure", error_source="upstream"),
            event(1, 1, 110, "retry"),
        ]
        full = [*first, event(2, 2, 120, "attempt"), event(2, 2, 620, "success")]
        self.add(
            1, kind=5, request="retry", meta=metadata(first, status_code=429), quota=0
        )
        self.add(2, channel=2, request="retry", meta=metadata(full, {"status": "ok"}))
        result = self.load(latency_scope="all")
        rows = {r["channel_id"]: r for r in result["rows"]}
        self.assertEqual(rows[1]["failure_count"], 1)
        self.assertEqual(rows[1]["failure_codes"], {"429": 1})
        self.assertEqual(rows[2]["success_count"], 1)
        self.assertEqual(rows[2]["duration_p50_ms"], 500)
        self.assertIsNone(rows[2]["frt_p50_ms"])
        self.assertIsNone(rows[1]["avg_tokens_per_second"])
        self.assertEqual(rows[1]["tps_samples"], 0)
        self.assertEqual(rows[2]["tps_output_tokens"], 100)
        self.assertEqual(rows[2]["avg_tokens_per_second"], 200)
        self.assertEqual(result["totals"]["request_count"], 2)
        self.assertEqual(result["totals"]["unique_requests"], 1)
        self.assertEqual(rows[1]["sample_request_ids"], ["retry"])
        self.assertEqual(rows[2]["sample_request_ids"], ["retry"])
        self.assertEqual(Decimal(result["totals"]["amount"]), Decimal("1"))
        direct = self.load()
        self.assertEqual(direct["totals"]["duration_samples"], 0)
        self.assertEqual(direct["totals"]["tps_samples"], 0)
        self.assertIsNone(direct["totals"]["avg_tokens_per_second"])
        upstream = self.load(mode="upstream", latency_scope="all")
        self.assertEqual(len(upstream["rows"]), 1)
        self.assertEqual(upstream["rows"][0]["success_rate"], 50)
        self.assertEqual(upstream["rows"][0]["failure_count"], 1)

    def test_error_codes_follow_attempt_not_final_channel_or_http_200(self):
        first = [
            event(1, 1, 0, "attempt"),
            event(1, 1, 10, "failure", status=200),
            event(1, 1, 10, "retry"),
        ]
        second = [
            *first,
            event(2, 1, 20, "attempt"),
            event(2, 1, 40, "failure"),
            event(2, 1, 40, "retry"),
        ]
        full = [*second, event(3, 3, 50, "attempt"), event(3, 3, 70, "failure")]
        for i, channel, policy, code in (
            (1, 1, first, "400"),
            (2, 1, second, "502"),
            (3, 3, full, "503"),
        ):
            self.add(
                i,
                channel=channel,
                kind=5,
                request="same-channel-retries",
                meta=metadata(policy, status_code=code),
                quota=0,
            )
        # An earlier attempt has no status evidence: the final channel's 500 is not its code.
        self.add(
            4,
            channel=2,
            kind=5,
            meta=metadata(
                [
                    event(1, 1, 0, "attempt"),
                    event(1, 1, 20, "failure"),
                    event(2, 2, 30, "attempt"),
                    event(2, 2, 60, "failure", status_code=500),
                ],
                status_code=500,
            ),
            quota=0,
        )
        result = self.load()
        rows = {r["channel_id"]: r for r in result["rows"]}
        self.assertEqual(rows[1]["failure_codes"], {"400": 1, "502": 1, "未知": 1})
        self.assertEqual(rows[2]["failure_codes"], {"500": 1})
        self.assertEqual(rows[3]["failure_codes"], {"503": 1})
        self.assertEqual(result["totals"]["failure_count"], 5)
        self.assertEqual(result["totals"]["unique_requests"], 2)
        self.assertEqual(Decimal(result["totals"]["amount"]), 0)

    def test_http_error_counts_match_usage_statistics(self):
        counts = {"400": 29, "500": 3, "502": 13, "503": 37}
        rows = []
        for code, count in counts.items():
            for _ in range(count):
                i = len(rows) + 1
                rows.append(
                    (
                        i,
                        1790784000 + i,
                        "e" + str(i),
                        json.dumps(metadata(status_code=code)),
                    )
                )
        with self.connect() as conn, conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO logs VALUES(%s,%s,5,'fixture',7,'fixture-key','gpt',1,
                    'default',0,3,false,%s,%s,0)""",
                rows,
            )
        result = self.load()
        self.assertEqual(result["totals"]["failure_count"], 82)
        self.assertEqual(result["rows"][0]["failure_codes"], counts)
        self.assertEqual(list(result["totals"]["failure_codes"]), list(counts))

    def test_output_speed_uses_paired_sums_and_excludes_invalid_or_failed_samples(self):
        for i, channel, tokens, elapsed in (
            (1, 1, 100, 1000),
            (2, 1, 900, 3000),
            (3, 2, 1000, 10000),
            (4, 1, 100, 0),
            (5, 1, None, 1000),
            (6, 1, 0, 1000),
            (7, 1, -10, 1000),
        ):
            self.add(
                i,
                channel=channel,
                output_tokens=tokens,
                stream=i != 1,
                meta=metadata(
                    [
                        event(1, channel, 0, "attempt"),
                        event(1, channel, elapsed, "success"),
                    ]
                ),
            )
        self.add(
            8,
            kind=5,
            quota=0,
            output_tokens=1000,
            meta=metadata([event(1, 1, 0, "attempt"), event(1, 1, 1000, "failure")]),
        )
        with self.connect() as conn:
            conn.execute("UPDATE logs SET created_at=created_at+3600 WHERE id=3")
        result = self.load()
        rows = {r["channel_id"]: r for r in result["rows"]}
        self.assertEqual(rows[1]["avg_tokens_per_second"], 250)
        self.assertEqual(rows[2]["avg_tokens_per_second"], 100)
        self.assertEqual(rows[1]["tps_samples"], 2)
        self.assertEqual(rows[1]["tps_coverage"], 33.33)
        totals = result["totals"]
        self.assertEqual(rows[1]["duration_total_ms"], 7000)
        self.assertEqual(rows[1]["duration_samples"], 6)
        self.assertAlmostEqual(rows[1]["avg_duration_ms"], 7000 / 6)
        self.assertEqual(totals["duration_total_ms"], 17000)
        self.assertEqual(totals["duration_samples"], 7)
        self.assertAlmostEqual(totals["avg_duration_ms"], 17000 / 7)
        self.assertEqual(totals["tps_output_tokens"], 2000)
        self.assertEqual(totals["tps_duration_ms"], 14000)
        self.assertAlmostEqual(totals["avg_tokens_per_second"], 2000 / 14)
        self.assertEqual(totals["tps_samples"], 3)
        self.assertEqual(
            [h["avg_tokens_per_second"] for h in result["trends"]], [250, 100]
        )
        self.assertAlmostEqual(
            self.load(mode="upstream")["rows"][0]["avg_tokens_per_second"], 2000 / 14
        )
        self.assertEqual(Decimal(totals["amount"]), 7)

    def test_health_models_merge_all_channels_and_rank_current_filtered_calls(self):
        with self.connect() as conn:
            conn.execute("UPDATE channels SET status=1 WHERE id=2")
        entries = [
            ("gpt", 1, "success", 100),
            ("gpt", 1, "success", 100),
            ("gpt", 1, "success", 100),
            ("gpt", 1, "success", 400),
            ("gpt", 2, "failure", 2000),
            ("claude", 1, "success", 500),
            ("claude", 2, "failure", 100),
            *[("m" + str(i), 1, "success", 1000) for i in range(6)],
        ]
        for i, (model, channel, outcome, duration) in enumerate(entries, 1):
            self.add(
                i,
                channel=channel,
                kind=2 if outcome == "success" else 5,
                quota=500000 if outcome == "success" else 0,
                output_tokens=0 if i == 1 else 100,
                meta=metadata(
                    [
                        event(1, channel, 0, "attempt"),
                        event(1, channel, duration, outcome),
                    ]
                ),
            )
            with self.connect() as conn:
                conn.execute("UPDATE logs SET model_name=%s WHERE id=%s", (model, i))
        self.add(14, meta="{broken")
        with self.connect() as conn:
            conn.execute("UPDATE logs SET model_name='zz-unknown' WHERE id=14")
        result = quality.load(self.args, with_trends=True)
        self.assertEqual(
            [r["model_name"] for r in result["top_models"]],
            ["gpt", "claude", "m0", "m1", "m2", "m3"],
        )
        gpt, claude = result["top_models"][:2]
        self.assertEqual(
            (
                gpt["request_count"],
                gpt["success_count"],
                gpt["failure_count"],
                gpt["success_rate"],
            ),
            (5, 4, 1, 80),
        )
        self.assertEqual(claude["success_rate"], 50)
        self.assertAlmostEqual(result["totals"]["avg_duration_ms"], 7200 / 11)
        self.assertEqual(result["totals"]["duration_samples"], 11)
        self.assertEqual(result["totals"]["tps_samples"], 10)
        self.assertEqual(result["totals"]["success_rate"], 84.62)
        scoped = quality.load(
            MultiDict([*self.args.items(), ("channel_id", "1"), ("model", "gpt")])
        )
        self.assertEqual(len(scoped["top_models"]), 1)
        self.assertEqual(scoped["top_models"][0]["success_rate"], 100)
        self.assertEqual(scoped["totals"]["avg_duration_ms"], 175)
        unknown = quality.load(MultiDict([*self.args.items(), ("model", "zz-unknown")]))
        self.assertIsNone(unknown["top_models"][0]["success_rate"])
        self.assertIsNone(unknown["totals"]["avg_duration_ms"])
        empty = quality.load(MultiDict([*self.args.items(), ("model", "absent")]))
        self.assertEqual(empty["top_models"], [])
        self.assertIsNone(empty["totals"]["avg_duration_ms"])

    def test_legacy_speed_and_bad_json_fallback_do_not_invent_retry_speed(self):
        self.add(1, output_tokens=300, meta=metadata(stream={"status": "ok"}))
        self.add(
            2,
            output_tokens=900,
            meta=metadata(
                stream={"status": "ok"}, admin_info={"use_channel": ["1", "2"]}
            ),
        )
        self.add(3, output_tokens=1000, meta="{broken")
        result = self.load(latency_scope="all")
        self.assertEqual(result["totals"]["avg_tokens_per_second"], 100)
        self.assertEqual(result["totals"]["tps_samples"], 1)
        self.assertEqual(result["totals"]["tps_output_tokens"], 300)
        self.assertEqual(Decimal(result["totals"]["amount"]), 3)
        with self.connect() as conn:
            conn.execute("ALTER TABLE logs DROP COLUMN completion_tokens")
        missing = self.load(latency_scope="all")
        self.assertEqual(missing["totals"]["duration_samples"], 2)
        self.assertEqual(missing["totals"]["tps_samples"], 0)
        self.assertIsNone(missing["totals"]["avg_tokens_per_second"])
        self.assertTrue(any("completion_tokens" in w for w in missing["warnings"]))
        self.assertEqual(Decimal(missing["totals"]["amount"]), 3)

    def test_client_cancel_business_error_and_stream_failure(self):
        self.add(
            1,
            meta=metadata(
                [event(1, 1, 0, "attempt"), event(1, 1, 100, "success")],
                {"status": "ok", "response_status": "completed"},
            ),
        )
        self.add(
            2,
            meta=metadata(
                [
                    event(1, 1, 0, "attempt"),
                    event(
                        1,
                        1,
                        200,
                        "stop",
                        decision={
                            "reason": "stream_not_successful",
                            "source": "system",
                        },
                    ),
                ],
                {"status": "error", "end_reason": "client_gone"},
            ),
        )
        self.add(
            3,
            meta=metadata(
                [
                    event(1, 1, 0, "attempt"),
                    event(
                        1,
                        1,
                        400,
                        "stop",
                        decision={
                            "reason": "stream_not_successful",
                            "source": "system",
                        },
                    ),
                ],
                {"status": "error", "response_status": "failed"},
            ),
        )
        self.add(
            4,
            kind=5,
            quota=0,
            meta=metadata(
                [
                    event(1, 1, 0, "attempt"),
                    event(
                        1,
                        1,
                        30,
                        "failure",
                        status=400,
                        error_code="invalid_request_error",
                        error_source="upstream",
                    ),
                ]
            ),
        )
        result = self.load()
        t = result["totals"]
        self.assertEqual(
            (t["success_count"], t["failure_count"], t["ignored_count"]), (1, 2, 1)
        )
        self.assertEqual(t["failure_codes"], {"400": 1, "流式失败（未记录错误码）": 1})
        self.assertEqual(t["success_rate"], 33.33)
        self.assertEqual(t["request_count"], 4)
        self.assertEqual(Decimal(t["amount"]), Decimal("3"))

    def test_unknown_json_missing_columns_and_numeric_prices(self):
        self.add(1, meta="{broken", stream=False, quota=1)
        self.add(2, meta=metadata(), stream=True, quota=1)
        self.add(3, meta=metadata(stream={"status": "ok"}), quota=1)
        result = self.load()
        self.assertEqual(result["totals"]["unknown_count"], 2)
        self.assertEqual(Decimal(result["totals"]["amount"]), Decimal("0.000006"))
        prices = result["rows"][0]["pricing_buckets"]
        self.assertTrue(
            any(p["prices"] and p["prices"]["input_price"] == "2" for p in prices)
        )
        with self.connect() as conn:
            conn.execute(
                "ALTER TABLE logs DROP COLUMN use_time, DROP COLUMN request_id, DROP COLUMN is_stream, DROP COLUMN completion_tokens"
            )
        missing = self.load()
        self.assertEqual(missing["totals"]["duration_samples"], 0)
        self.assertEqual(missing["totals"]["tps_samples"], 0)
        self.assertIsNone(missing["totals"]["avg_tokens_per_second"])
        self.assertTrue(any("缺少字段" in w for w in missing["warnings"]))

    def test_current_tags_filters_and_global_percentiles(self):
        for i, channel, elapsed in [(1, 1, 100), (2, 2, 1000), (3, 2, 2000)]:
            self.add(
                i,
                channel=channel,
                meta=metadata(
                    [
                        event(1, channel, 0, "attempt"),
                        event(1, channel, elapsed, "success"),
                    ]
                ),
            )
        result = self.load(mode="upstream")
        self.assertEqual(result["totals"]["duration_p50_ms"], 1000)
        self.assertEqual(result["rows"][0]["duration_p95_ms"], 1900)
        self.assertEqual(len(result["trends"]), 1)
        selected = quality.load(
            MultiDict(
                [*self.args.items(), ("channel_id", "2"), ("channel_status", "all")]
            ),
        )
        self.assertEqual(selected["totals"]["request_count"], 2)
        self.assertEqual(selected["rows"][0]["pricing_buckets"], [])
        with self.connect() as conn:
            conn.execute("UPDATE channels SET tag='other' WHERE id=2")
        changed = self.load(tag="supplier")
        self.assertEqual(changed["totals"]["request_count"], 1)

    def test_all_billing_groups_tiers_and_upstreams_are_included_and_null_model(self):
        self.add(
            1,
            meta=metadata(
                [
                    {**event(1, 1, 0, "attempt"), "group": "retry-group"},
                    {**event(1, 1, 100, "success"), "group": "retry-group"},
                ]
            ),
        )
        self.add(
            2,
            channel=3,
            meta=metadata(
                [event(1, 3, 0, "attempt"), event(1, 3, 200, "success")],
                matched_tier="high",
            ),
        )
        with self.connect() as conn:
            conn.execute("INSERT INTO channels VALUES(4,'untagged',1,NULL)")
            conn.execute("UPDATE logs SET \"group\"='vip' WHERE id=2")
        self.add(
            3,
            channel=4,
            meta=metadata([event(1, 4, 0, "attempt"), event(1, 4, 300, "success")]),
        )
        result = self.load()
        self.assertEqual(result["totals"]["success_count"], 3)
        self.assertEqual(Decimal(result["totals"]["amount"]), 3)
        self.assertEqual(
            {r["tag_value"] for r in result["rows"]}, {"supplier", "other", ""}
        )
        self.assertNotIn("scope", result)
        self.assertNotIn("groups", result["options"])
        self.assertNotIn("tiers", result["options"])
        with self.connect() as conn:
            conn.execute(
                "UPDATE logs SET model_name=NULL,username=NULL,token_id=NULL,token_name=NULL"
            )
        unknown = self.load()
        self.assertEqual(len(unknown["rows"]), 3)
        self.assertEqual(Decimal(unknown["totals"]["amount"]), 3)
