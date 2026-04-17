#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autoalerter: create/update incidents based on monitoring events."""

from __future__ import annotations

import argparse
import json
import logging
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
import urllib3

import cnf
from db import (
    close_open_incidents,
    create_internal_incident,
    get_active_incident_count,
    get_last_closed_incident_for_reopen,
    get_last_jira_issue_key,
    get_last_thread_root_event_id,
    mark_last_closed_incident_timestamp,
    update_event_counter,
)
from jira_client import JiraServiceData, create_jira_incident, get_jira_data, validate_jira_incident_status
from ktalk_invites import build_dry_run_invite_message, get_room_members, invite_missing_users_to_room
from ktalk_messenger import create_discussion, mark_discussion_resolved, mention_users_in_thread, send_to_ktalk_message

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r".*The 'im' API section is deprecated.*",
)

logger = logging.getLogger("autoalerter")


@dataclass(slots=True)
class EventPayload:
    event: str
    insight_id: str
    groups: str
    trigger_time: str
    trigger_name: str
    message: str


@dataclass(slots=True)
class ResolvedRecipient:
    ad_login: str
    ktalk_mention_id: str
    ad_name: str


def _sanitize_log_message(message: str) -> str:
    sanitized = re.sub(r"(?i)bearer\s+[a-z0-9._\-=+/]+", "Bearer ***", message)
    sanitized = re.sub(r"(?i)(authorization\s*[:=]\s*)([^\s,]+)", r"\1***", sanitized)
    sanitized = re.sub(r"(?i)(password\s*[:=]\s*)([^\s,]+)", r"\1***", sanitized)
    sanitized = re.sub(r"(?i)(token\s*[:=]\s*)([^\s,]+)", r"\1***", sanitized)
    return sanitized


class _SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _sanitize_log_message(record.getMessage())
        record.args = ()
        return True


def configure_logging() -> None:
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return

    handler = logging.FileHandler(cnf.LOG_FILE, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))
    handler.addFilter(_SecretFilter())
    logger.addHandler(handler)


def parse_args() -> EventPayload:
    parser = argparse.ArgumentParser(description="Auto incident handler")
    parser.add_argument("--event", required=True, choices=["0", "1"])
    parser.add_argument("--insightId", required=True)
    parser.add_argument("--groups", required=True)
    parser.add_argument("--triggerTime", required=True)
    parser.add_argument("--trigName", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    return EventPayload(
        event=args.event,
        insight_id=args.insightId,
        groups=args.groups,
        trigger_time=args.triggerTime,
        trigger_name=args.trigName,
        message=args.message,
    )


def validate_payload(payload: EventPayload) -> None:
    logger.info("Step: validate input payload")
    if not re.fullmatch(r"^TZ-\d+", payload.insight_id):
        raise ValueError(f"Invalid insightId: {payload.insight_id}")


def extract_short_name_from_groups(groups: str) -> str:
    match = re.search(r"SG/([^,/]+)", groups)
    if not match:
        raise ValueError("Cannot extract short_name from groups")
    return match.group(1)


def format_trigger_time_for_database(trigger_time: str) -> str:
    trigger_dt = datetime.strptime(trigger_time, "%Y.%m.%d %H:%M:%S")
    return trigger_dt.strftime("%Y-%m-%d %H:%M:%S.000 +0300")


def default_issue_template() -> dict[str, dict[str, object]]:
    return {
        "fields": {
            "project": {"id": "22601"},
            "issuetype": {"id": "10120"},
            "summary": "",
            "description": "",
            "customfield_27602": [{"key": "TZ-24771"}],
            "customfield_35901": [{"key": "TZ-30904"}],
            "customfield_19700": [],
        }
    }


def _normalize_login(raw_login: str) -> str:
    normalized_login = str(raw_login).strip().lower()
    if not normalized_login:
        return ""
    if normalized_login.startswith("@"):
        normalized_login = normalized_login[1:]
    if ":" in normalized_login:
        normalized_login = normalized_login.split(":", 1)[0]
    return normalized_login.strip().lower()


def merge_requested_logins_with_mandatory(jira_recipients: list[str]) -> list[str]:
    merged_logins: list[str] = []
    seen_logins: set[str] = set()

    for source_recipients in (jira_recipients, list(cnf.MANDATORY_RECIPIENTS)):
        for recipient_login in source_recipients:
            normalized_login = _normalize_login(recipient_login)
            if normalized_login and normalized_login not in seen_logins:
                seen_logins.add(normalized_login)
                merged_logins.append(normalized_login)

    logger.info(
        "Step: build recipients list from Jira + mandatory | jira=%s merged=%s",
        len(jira_recipients),
        len(merged_logins),
    )
    return merged_logins


def _extract_resolved_recipients_from_resolver_payload(payload: dict[str, object]) -> list[ResolvedRecipient]:
    users_field = payload.get("users", [])
    if not isinstance(users_field, list):
        raise ValueError("Resolver response field 'users' must be a list")

    resolved_recipients: list[ResolvedRecipient] = []
    for user_item in users_field:
        if not isinstance(user_item, dict):
            continue

        ad_login = _normalize_login(user_item.get("ad_login", ""))
        ktalk_mention_id = str(user_item.get("ktalk_mention_id", "")).strip()
        ad_name = str(user_item.get("ad_name", "")).strip()

        if not ad_login or not ktalk_mention_id:
            continue

        resolved_recipients.append(
            ResolvedRecipient(
                ad_login=ad_login,
                ktalk_mention_id=ktalk_mention_id,
                ad_name=ad_name,
            )
        )

    return resolved_recipients


def resolve_recipients_via_api(requested_logins: list[str]) -> list[ResolvedRecipient]:
    if not requested_logins:
        logger.info("Step: resolve recipients via API skipped (no requested logins)")
        return []

    query_string = urlencode({"ad_login": requested_logins}, doseq=True)
    resolver_url = f"{cnf.RECIPIENT_RESOLVER_URL}?{query_string}"

    logger.info("Step: resolve recipients via API | requested=%s", len(requested_logins))

    try:
        response = requests.get(
            resolver_url,
            verify=cnf.VERIFY_SSL,
            timeout=cnf.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        logger.error("Resolver API request failed: %s", error)
        return []

    try:
        resolver_payload = response.json()
    except json.JSONDecodeError as error:
        logger.error("Resolver API returned invalid JSON: %s", error)
        return []

    if not isinstance(resolver_payload, dict):
        logger.error("Resolver API returned unexpected payload type: %s", type(resolver_payload).__name__)
        return []

    try:
        resolved_recipients = _extract_resolved_recipients_from_resolver_payload(resolver_payload)
    except ValueError as error:
        logger.error("Resolver API response validation failed: %s", error)
        return []

    not_found_ad_logins = resolver_payload.get("not_found_ad_logins", []) or []
    without_mention_ad_logins = resolver_payload.get("without_ktalk_mention_id", []) or []

    logger.info(
        "Step: resolver result | requested=%s resolved=%s not_found=%s without_mention=%s",
        len(requested_logins),
        len(resolved_recipients),
        len(not_found_ad_logins),
        len(without_mention_ad_logins),
    )
    return resolved_recipients


def split_recipients_by_room_membership(
    resolved_recipients: list[ResolvedRecipient],
    room_members: set[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    in_room_recipients: list[dict[str, str]] = []
    out_of_room_recipients: list[dict[str, str]] = []

    for recipient in resolved_recipients:
        recipient_payload = {
            "ad_login": recipient.ad_login,
            "ktalk_mention_id": recipient.ktalk_mention_id,
            "ad_name": recipient.ad_name,
        }
        if recipient.ktalk_mention_id in room_members:
            in_room_recipients.append(recipient_payload)
        else:
            out_of_room_recipients.append(recipient_payload)

    logger.info(
        "Step: split recipients by room presence | total=%s in_room=%s out_of_room=%s",
        len(resolved_recipients),
        len(in_room_recipients),
        len(out_of_room_recipients),
    )
    return in_room_recipients, out_of_room_recipients


def get_current_local_datetime() -> datetime:
    return datetime.now(ZoneInfo(cnf.TIMEZONE_NAME))


def is_night_window_active(current_local_datetime: datetime) -> bool:
    start_hour = int(cnf.FLAP_REOPEN_TIME_START_HOUR)
    end_hour = int(cnf.FLAP_REOPEN_TIME_END_HOUR)
    current_hour = current_local_datetime.hour

    if start_hour == end_hour:
        return True

    if start_hour < end_hour:
        return start_hour <= current_hour < end_hour

    return current_hour >= start_hour or current_hour < end_hour


def is_flap_reopen_schedule_active(current_local_datetime: datetime) -> bool:
    if not cnf.FLAP_REOPEN_WINDOW_ENABLED:
        logger.info("Flap reopen disabled by configuration")
        return False

    weekday_value = current_local_datetime.weekday()  # 0=Monday ... 6=Sunday
    is_weekday_in_config = weekday_value in set(cnf.FLAP_REOPEN_WEEKDAYS)
    is_night_period = bool(cnf.FLAP_REOPEN_APPLY_NIGHT_WINDOW and is_night_window_active(current_local_datetime))

    if is_weekday_in_config or is_night_period:
        logger.info(
            "Flap reopen schedule active | weekday=%s is_weekday_in_config=%s is_night_period=%s",
            weekday_value,
            is_weekday_in_config,
            is_night_period,
        )
        return True

    logger.info("Current time is outside configured flap-reopen schedule")
    return False


def _convert_datetime_to_local_timezone(value: datetime) -> datetime:
    local_timezone = ZoneInfo(cnf.TIMEZONE_NAME)
    if value.tzinfo is None:
        return value.replace(tzinfo=local_timezone)
    return value.astimezone(local_timezone)


def get_reopen_candidate_from_recent_close(insight_id: str, current_local_datetime: datetime) -> tuple[str, str] | None:
    if not is_flap_reopen_schedule_active(current_local_datetime):
        return None

    closed_incident_candidate = get_last_closed_incident_for_reopen(insight_id)
    if not closed_incident_candidate:
        logger.info("No recently closed incident found for reopen")
        return None

    close_event_at_local = _convert_datetime_to_local_timezone(closed_incident_candidate.close_event_at)
    close_age = current_local_datetime - close_event_at_local
    close_age_minutes = int(close_age.total_seconds() / 60)
    logger.info(
        "Recent closed incident found | closed_at=%s age_minutes=%s",
        close_event_at_local.isoformat(),
        close_age_minutes,
    )

    reopen_window = timedelta(hours=int(cnf.FLAP_REOPEN_HOURS))
    if close_age < timedelta(0) or close_age > reopen_window:
        logger.info("Recent close is older than reopen window, creating new incident")
        return None

    logger.info(
        "Reusing existing thread_root_event_id=%s jira_issue_key=%s",
        closed_incident_candidate.thread_root_event_id,
        closed_incident_candidate.jira_issue_key,
    )
    return closed_incident_candidate.thread_root_event_id, closed_incident_candidate.jira_issue_key


def _send_message_to_existing_thread(payload: EventPayload, thread_root_event_id: str) -> None:
    send_to_ktalk_message(
        payload.message,
        payload.trigger_time,
        cnf.KTALK_ROOM_ID,
        payload.event,
        thread_id=thread_root_event_id,
    )


def _process_existing_open_incident(payload: EventPayload) -> bool:
    logger.info("Step: decide active incident existence")
    active_incident_count = get_active_incident_count(payload.insight_id)
    if active_incident_count == 0:
        logger.info("Decision: active incident not found; create/reopen flow")
        return False

    updated_event_balance = update_event_counter(payload.insight_id, delta=1)
    logger.info("Step: update event counter for existing incident | new_balance=%s", updated_event_balance)

    thread_root_event_id = get_last_thread_root_event_id(payload.insight_id)
    if not thread_root_event_id:
        logger.warning("Existing incident found but thread_root_event_id is missing")
        return True

    logger.info("Step: send message to existing KTalk thread")
    _send_message_to_existing_thread(payload, thread_root_event_id)
    return True


def _persist_reopened_incident(
    payload: EventPayload,
    jira_service_data: JiraServiceData,
    short_name: str,
    requested_logins: list[str],
    thread_root_event_id: str,
    jira_issue_key: str,
) -> None:
    create_internal_incident(
        insight_id=payload.insight_id,
        short_name=short_name,
        full_name=jira_service_data.full_name,
        trigger_name=payload.trigger_name,
        recipients=requested_logins,
        trigger_start_time=format_trigger_time_for_database(payload.trigger_time),
        thread_root_event_id=thread_root_event_id,
        jira_issue_key=jira_issue_key,
    )


def _try_reopen_recent_closed_incident(
    payload: EventPayload,
    jira_service_data: JiraServiceData,
    short_name: str,
    requested_logins: list[str],
) -> bool:
    current_local_datetime = get_current_local_datetime()
    reopen_candidate = get_reopen_candidate_from_recent_close(payload.insight_id, current_local_datetime)
    if not reopen_candidate:
        return False

    reopen_thread_root_event_id, reopen_jira_issue_key = reopen_candidate

    logger.info("Step: anti-flap reopen path activated")
    _persist_reopened_incident(
        payload=payload,
        jira_service_data=jira_service_data,
        short_name=short_name,
        requested_logins=requested_logins,
        thread_root_event_id=reopen_thread_root_event_id,
        jira_issue_key=reopen_jira_issue_key,
    )
    _send_message_to_existing_thread(payload, reopen_thread_root_event_id)
    return True


def _notify_recipients_in_ktalk(requested_logins: list[str], thread_root_event_id: str) -> None:
    resolved_recipients = resolve_recipients_via_api(requested_logins)
    if not resolved_recipients:
        logger.info("Step: resolver returned no recipients; skip mention/invite")
        logger.info("KTalk mention summary | mentioned_count=0")
        if cnf.KTALK_INVITES_DRY_RUN:
            logger.info("KTalk invite dry-run summary | would_invite_count=0 actual_invited_count=0")
        else:
            logger.info("KTalk invite summary | invited_count=0")
        return

    logger.info("Step: load room members")
    room_members = get_room_members(cnf.KTALK_ROOM_ID)
    logger.info("Step: room members loaded | count=%s", len(room_members))

    in_room_recipients, out_of_room_recipients = split_recipients_by_room_membership(
        resolved_recipients,
        room_members,
    )

    mentioned_count = 0
    if in_room_recipients:
        logger.info("Step: mention in-room users")
        mentioned_count = mention_users_in_thread(in_room_recipients, cnf.KTALK_ROOM_ID, thread_root_event_id)
    logger.info("KTalk mention summary | mentioned_count=%s", mentioned_count)

    if not out_of_room_recipients:
        if cnf.KTALK_INVITES_DRY_RUN:
            logger.info("KTalk invite dry-run summary | would_invite_count=0 actual_invited_count=0")
        else:
            logger.info("KTalk invite summary | invited_count=0")
        logger.info("KTalk notify summary | mentioned_count=%s invited_count=0", mentioned_count)
        return

    invite_result = invite_missing_users_to_room(
        out_of_room_recipients,
        cnf.KTALK_ROOM_ID,
        dry_run_enabled=bool(cnf.KTALK_INVITES_DRY_RUN),
    )

    if invite_result.dry_run_enabled:
        dry_run_message = build_dry_run_invite_message(invite_result.dry_run_users)
        send_to_ktalk_message(
            dry_run_message,
            "",
            cnf.KTALK_ROOM_ID,
            event="1",
            thread_id=None,
            message_format="plain",
        )
        logger.info(
            "KTalk invite dry-run summary | would_invite_count=%s actual_invited_count=0 users=%s",
            invite_result.would_invite_count,
            [item.get("ad_login", "") for item in invite_result.dry_run_users],
        )
        logger.info(
            "KTalk notify summary | mentioned_count=%s invited_count=0 would_invite_count=%s",
            mentioned_count,
            invite_result.would_invite_count,
        )
        return

    logger.info("KTalk invite summary | invited_count=%s", invite_result.invited_count)
    logger.info(
        "KTalk notify summary | mentioned_count=%s invited_count=%s",
        mentioned_count,
        invite_result.invited_count,
    )


def _create_new_incident_flow(payload: EventPayload, jira_service_data: JiraServiceData, short_name: str, requested_logins: list[str]) -> None:
    logger.info("Step: create Jira incident")
    jira_create_response = create_jira_incident(
        payload.insight_id,
        short_name,
        payload.trigger_name,
        issue_data=default_issue_template(),
        function_object_key=jira_service_data.function_object_key,
        jira_incident_type_key=jira_service_data.jira_incident_type_key,
    )
    jira_issue_key = str(jira_create_response.get("key", "")).strip()

    logger.info("Step: create KTalk discussion (root + first reply)")
    thread_root_event_id = create_discussion(
        jira_service_data.full_name,
        payload.trigger_name,
        payload.message,
        jira_issue_key,
        payload.trigger_time,
    )

    _notify_recipients_in_ktalk(requested_logins, thread_root_event_id)

    logger.info("Step: persist incident state in database")
    create_internal_incident(
        insight_id=payload.insight_id,
        short_name=short_name,
        full_name=jira_service_data.full_name,
        trigger_name=payload.trigger_name,
        recipients=requested_logins,
        trigger_start_time=format_trigger_time_for_database(payload.trigger_time),
        thread_root_event_id=thread_root_event_id,
        jira_issue_key=jira_issue_key,
    )


def process_open_event(payload: EventPayload) -> None:
    logger.info("Step: process event=1 (open)")

    logger.info("Step: load service metadata from Jira")
    jira_service_data = get_jira_data(payload.insight_id)
    if not jira_service_data.is_actual:
        logger.info("Decision: service is not actual; skip open-event")
        return

    if _process_existing_open_incident(payload):
        return

    short_name = extract_short_name_from_groups(payload.groups)
    requested_logins = merge_requested_logins_with_mandatory(jira_service_data.recipients)

    if _try_reopen_recent_closed_incident(payload, jira_service_data, short_name, requested_logins):
        return

    _create_new_incident_flow(payload, jira_service_data, short_name, requested_logins)


def process_close_event(payload: EventPayload) -> None:
    logger.info("Step: process event=0 (close)")

    logger.info("Step: load active thread and Jira issue key for close path")
    thread_root_event_id = get_last_thread_root_event_id(payload.insight_id)
    jira_issue_key = get_last_jira_issue_key(payload.insight_id)

    updated_event_balance = update_event_counter(payload.insight_id, delta=-1)
    logger.info("Step: update event counter for close event | new_balance=%s", updated_event_balance)

    if thread_root_event_id:
        logger.info("Step: send close-event message to existing thread")
        send_to_ktalk_message(
            payload.message,
            format_trigger_time_for_database(payload.trigger_time),
            cnf.KTALK_ROOM_ID,
            payload.event,
            thread_id=thread_root_event_id,
        )
    else:
        logger.warning("No thread_root_event_id found for close event")

    if updated_event_balance > 0:
        logger.info("Decision: event balance is still positive, keep incident open")
        return

    logger.info("Decision: event balance reached zero, finalize incident")
    close_timestamp_saved = mark_last_closed_incident_timestamp(payload.insight_id)
    logger.info("Step: mark close_event_at for last closed incident | saved=%s", close_timestamp_saved)

    if jira_issue_key:
        jira_issue_is_active = validate_jira_incident_status(jira_issue_key)
        logger.info("Step: Jira status validated | issue=%s is_active=%s", jira_issue_key, jira_issue_is_active)
    else:
        logger.warning("No Jira issue key found for insight_id=%s", payload.insight_id)

    if thread_root_event_id:
        mark_discussion_resolved(thread_root_event_id)
        send_to_ktalk_message(
            "Активные алерты, на которые был создан инцидент, отсутствуют",
            format_trigger_time_for_database(payload.trigger_time),
            cnf.KTALK_ROOM_ID,
            payload.event,
            thread_id=thread_root_event_id,
        )

    logger.info("Step: close open incidents in database")
    close_open_incidents(payload.insight_id)


def main() -> int:
    configure_logging()
    logger.info("Autoalerter started")

    try:
        logger.info("Step: parse incoming event payload")
        payload = parse_args()
        logger.info("Incoming event payload received | event=%s insight_id=%s", payload.event, payload.insight_id)

        validate_payload(payload)

        if payload.event == "1":
            process_open_event(payload)
        else:
            active_incident_count = get_active_incident_count(payload.insight_id)
            if active_incident_count == 0:
                logger.info("Step: close-event ignored because there is no active incident in DB")
                return 0
            process_close_event(payload)

        logger.info("Autoalerter finished successfully")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error during autoalerter execution: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
