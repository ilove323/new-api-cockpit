"""Monitoring-database advisory locks shared across application processes.

Session and transaction locks share the same PostgreSQL identifier space.
Keep existing balance/notification IDs stable; scheduler leadership must have
its own ID. Do not change these IDs during a rolling worker replacement.
"""

BALANCE_LOCK = 90216321
NOTIFICATION_LOCK = 90216322
QUOTA_SCHEDULER_LOCK = 90216323
