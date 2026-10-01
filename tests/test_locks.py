"""Advisory lock identifiers must not collide across monitoring subsystems."""

import unittest
from unittest.mock import MagicMock, patch

from new_api_statistics import locks, notifications, quota_schedule


class AdvisoryLockTest(unittest.TestCase):
    def test_identifiers_are_distinct_and_existing_transactions_keep_their_ids(self):
        self.assertEqual(locks.BALANCE_LOCK, 90216321)
        self.assertEqual(locks.NOTIFICATION_LOCK, 90216322)
        self.assertEqual(quota_schedule.LEADER_LOCK, locks.QUOTA_SCHEDULER_LOCK)
        self.assertEqual(
            len(
                {
                    locks.BALANCE_LOCK,
                    locks.NOTIFICATION_LOCK,
                    locks.QUOTA_SCHEDULER_LOCK,
                    locks.BALANCE_SCHEDULER_LOCK,
                }
            ),
            4,
        )

    def test_notification_delivery_uses_its_own_lock(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "channel": "feishu_app",
            "enabled": False,
        }
        with (
            patch.object(notifications.balance, "connect") as connect,
            patch.object(notifications, "provider_config", return_value={}),
        ):
            connect.return_value.__enter__.return_value = conn
            notifications.deliver()
        conn.execute.assert_any_call(
            "SELECT pg_advisory_xact_lock(%s)", (locks.NOTIFICATION_LOCK,)
        )
