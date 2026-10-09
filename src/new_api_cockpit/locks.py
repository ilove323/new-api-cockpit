"""Monitoring-database advisory locks shared across application processes.

Session and transaction locks share the same PostgreSQL identifier space.
Keep existing balance/notification IDs stable; scheduler leadership must have
its own ID. Do not change these IDs during a rolling worker replacement.
"""

BALANCE_LOCK = 90216321
NOTIFICATION_LOCK = 90216322
QUOTA_SCHEDULER_LOCK = 90216323

# Two-key advisory-lock namespace; it cannot collide with the bigint locks above.
REPORT_SNAPSHOT_NAMESPACE = 90216324

# A session leader lock, never the short-lived balance transaction lock.
BALANCE_SCHEDULER_LOCK = 90216325

# Per-administrator, request-scoped model testing. No task or result persistence.
INTELLIGENCE_NAMESPACE = 90216326
