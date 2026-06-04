# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import psycopg2
from psycopg2.extensions import connection as PgConnection

logger = logging.getLogger("autoalerter")

DB_HOST = "localhost"
DB_NAME = "trmetrics"
DB_USER = "user"
DB_PASSWORD = "password"
DB_PORT = 5432
DB_OPTIONS = ""

SQL_GET_LAST_THREAD_ROOT_EVENT_ID = """
SELECT r_discussion_id
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
ORDER BY id DESC
LIMIT 1;
"""

SQL_GET_LAST_JIRA_ISSUE_KEY = """
SELECT jira_issue_key
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
ORDER BY id DESC
LIMIT 1;
"""

SQL_CLOSE_INCIDENTS = """
UPDATE trmetrics.availconf.conf
SET event_balance = 0,
    close_event_at = CURRENT_TIMESTAMP
WHERE insight_id = %(insight_id)s
  AND event_balance > 0;
"""

SQL_COUNT_ACTIVE_INCIDENTS = """
SELECT COUNT(*)
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance > 0;
"""

SQL_CREATE_INCIDENT = """
INSERT INTO trmetrics.availconf.conf (
    insight_id,
    short_name,
    full_name,
    trigger_name,
    trigger_time,
    time_start,
    recipients,
    r_discussion_id,
    event_balance,
    jira_issue_key
)
VALUES (
    %(insight_id)s,
    %(short_name)s,
    %(full_name)s,
    %(trigger_name)s,
    %(trigger_time)s,
    CURRENT_TIMESTAMP,
    %(recipients)s,
    %(rdiscussionid)s,
    1,
    %(jira_issue_key)s
);
"""

SQL_UPDATE_EVENT_COUNTER = """
UPDATE trmetrics.availconf.conf
SET event_balance = GREATEST(COALESCE(event_balance, 0) + %(delta)s, 0)
WHERE insight_id = %(insight_id)s
  AND event_balance > 0
RETURNING event_balance;
"""


SQL_GET_LAST_CLOSED_INCIDENT_FOR_REOPEN = """
SELECT
    close_event_at,
    r_discussion_id,
    jira_issue_key
FROM trmetrics.availconf.conf
WHERE insight_id = %(insight_id)s
  AND event_balance = 0
  AND close_event_at IS NOT NULL
ORDER BY close_event_at DESC
LIMIT 1;
"""

SQL_MARK_LAST_CLOSED_INCIDENT_TIMESTAMP = """
UPDATE trmetrics.availconf.conf
SET close_event_at = CURRENT_TIMESTAMP
WHERE id = (
    SELECT id
    FROM trmetrics.availconf.conf
    WHERE insight_id = %(insight_id)s
      AND event_balance = 0
    ORDER BY id DESC
    LIMIT 1
)
RETURNING id;
"""


@dataclass(slots=True)
class ClosedIncidentReopenCandidate:
    close_event_at: Any
    thread_root_event_id: str
    jira_issue_key: str


def get_db_connection() -> PgConnection:
    logger.debug("Opening PostgreSQL connection: host=%s db=%s", DB_HOST, DB_NAME)
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        options=DB_OPTIONS,
    )


def get_active_incident_count(insight_id: str) -> int:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(SQL_COUNT_ACTIVE_INCIDENTS, {"insight_id": insight_id})
        row = cur.fetchone()
    return int(row[0]) if row else 0


def get_last_thread_root_event_id(insight_id: str) -> str | None:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(SQL_GET_LAST_THREAD_ROOT_EVENT_ID, {"insight_id": insight_id})
        row = cur.fetchone()
    return str(row[0]).strip() if row and row[0] else None


def get_last_jira_issue_key(insight_id: str) -> str | None:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(SQL_GET_LAST_JIRA_ISSUE_KEY, {"insight_id": insight_id})
        row = cur.fetchone()
    return str(row[0]).strip() if row and row[0] else None


def close_open_incidents(insight_id: str) -> None:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(SQL_CLOSE_INCIDENTS, {"insight_id": insight_id})


def update_event_counter(insight_id: str, delta: int) -> int:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(SQL_UPDATE_EVENT_COUNTER, {"insight_id": insight_id, "delta": delta})
        row = cur.fetchone()

    if row is None:
        raise RuntimeError(f"update_event_counter returned no rows for insight_id={insight_id}")

    return int(row[0])


def create_internal_incident(
    insight_id: str,
    short_name: str,
    full_name: str,
    trigger_name: str,
    recipients: list[str],
    trigger_start_time: str,
    thread_root_event_id: str | None,
    jira_issue_key: str,
) -> None:
    values: dict[str, Any] = {
        "insight_id": insight_id,
        "short_name": short_name,
        "full_name": full_name,
        "trigger_name": trigger_name,
        "recipients": recipients,
        "trigger_time": trigger_start_time,
        "rdiscussionid": thread_root_event_id or "",
        "jira_issue_key": jira_issue_key,
    }
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(SQL_CREATE_INCIDENT, values)


def get_last_closed_incident_for_reopen(insight_id: str) -> ClosedIncidentReopenCandidate | None:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(SQL_GET_LAST_CLOSED_INCIDENT_FOR_REOPEN, {"insight_id": insight_id})
        row = cur.fetchone()

    if not row:
        return None

    close_event_at, thread_root_event_id, jira_issue_key = row
    if not close_event_at or not thread_root_event_id or not jira_issue_key:
        return None

    return ClosedIncidentReopenCandidate(
        close_event_at=close_event_at,
        thread_root_event_id=str(thread_root_event_id).strip(),
        jira_issue_key=str(jira_issue_key).strip(),
    )


def mark_last_closed_incident_timestamp(insight_id: str) -> bool:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(SQL_MARK_LAST_CLOSED_INCIDENT_TIMESTAMP, {"insight_id": insight_id})
        row = cur.fetchone()
    return row is not None


def get_last_thread_id(insight_id: str) -> str | None:
    """Backward-compatible alias for old function name."""
    return get_last_thread_root_event_id(insight_id)
