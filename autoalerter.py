#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autoalerter: create/update incidents based on monitoring events."""

from __future__ import annotations

import argparse
import logging
import re
import warnings
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

import requests
import urllib3

import cnf
from db import (
    close_open_incidents,
    create_internal_incident,
    get_active_incident_count,
    get_last_jira_issue_key,
    get_last_thread_id,
    update_event_counter,
)
from jira_client import create_jira_incident, get_jira_data, validate_jira_incident_status
from ktalk_invites import get_room_members, invite_missing_users_to_room
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
        msg = record.getMessage()
        record.msg = _sanitize_log_message(msg)
        record.args = ()
        return True


def configure_logging() -> None:
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        logger.debug("Logger already configured, skip reconfiguration")
        return

    handler = logging.FileHandler(cnf.LOG_FILE, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    )
    handler.addFilter(_SecretFilter())
    logger.addHandler(handler)
    logger.debug("Logging configured. file=%s level=DEBUG", cnf.LOG_FILE)


def parse_args() -> EventPayload:
    logger.debug("Parsing CLI arguments")
    parser = argparse.ArgumentParser(description="Auto incident handler")
    parser.add_argument("--event", required=True, choices=["0", "1"])
    parser.add_argument("--insightId", required=True)
    parser.add_argument("--groups", required=True)
    parser.add_argument("--triggerTime", required=True)
    parser.add_argument("--trigName", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    payload = EventPayload(
        event=args.event,
        insight_id=args.insightId,
        groups=args.groups,
        trigger_time=args.triggerTime,
        trigger_name=args.trigName,
        message=args.message,
    )
    logger.debug(
        "Args parsed: event=%s insight_id=%s trigger_name=%s",
        payload.event,
        payload.insight_id,
        payload.trigger_name,
    )
    return payload


def validate_insight_id(insight_id: str) -> str:
    logger.debug("Validating insight_id=%s", insight_id)
    if not re.fullmatch(r"^TZ-\d+", insight_id):
        raise ValueError(f"Invalid insightId: {insight_id}")
    return insight_id


def extract_shortname(groups: str) -> str:
    logger.debug("Extracting shortname from groups=%r", groups)
    match = re.search(r"SG/([^,/]+)", groups)
    if not match:
        raise ValueError("Cannot extract shortname from groups")
    return match.group(1)


def convert_trigger_time(trigger_time: str) -> str:
    dt = datetime.strptime(trigger_time, "%Y.%m.%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S.000 +0300")


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


def _normalize_login(value: str) -> str:
    login = str(value).strip().lower()
    if not login:
        return ""
    if login.startswith("@"):
        login = login[1:]
    if ":" in login:
        login = login.split(":", 1)[0]
    return login.strip().lower()


def merge_recipients_with_mandatory(users: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for source in (users, list(cnf.MANDATORY_RECIPIENTS)):
        for item in source:
            normalized = _normalize_login(item)
            if normalized and normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)

    logger.info("Recipients merged requested=%s total=%s", len(users), len(merged))
    return merged


def resolve_recipients_via_api(ad_logins: list[str]) -> list[ResolvedRecipient]:
    if not ad_logins:
        return []

    query = urlencode({"ad_login": ad_logins}, doseq=True)
    resolver_url = f"{cnf.RECIPIENT_RESOLVER_URL}?{query}"
    logger.debug("Resolving recipients via API url=%s count=%s", resolver_url, len(ad_logins))

    response = requests.get(
        resolver_url,
        verify=cnf.VERIFY_SSL,
        timeout=cnf.REQUEST_TIMEOUT,
    )
    logger.debug("Resolver response status=%s", response.status_code)
    response.raise_for_status()

    payload = response.json()
    users = payload.get("users", [])
    if not isinstance(users, list):
        raise ValueError("Resolver response field 'users' is not a list")

    resolved: list[ResolvedRecipient] = []
    for item in users:
        if not isinstance(item, dict):
            continue
        ad_login = _normalize_login(item.get("ad_login", ""))
        mention_id = str(item.get("ktalk_mention_id", "")).strip()
        ad_name = str(item.get("ad_name", "")).strip()
        if not ad_login or not mention_id:
            continue
        resolved.append(
            ResolvedRecipient(
                ad_login=ad_login,
                ktalk_mention_id=mention_id,
                ad_name=ad_name,
            )
        )

    logger.info(
        "Resolver result requested=%s resolved=%s not_found=%s without_mention=%s",
        len(ad_logins),
        len(resolved),
        len(payload.get("not_found_ad_logins", []) or []),
        len(payload.get("without_ktalk_mention_id", []) or []),
    )
    return resolved


def split_recipients_by_room_members(
    recipients: list[ResolvedRecipient],
    room_members: set[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    in_room: list[dict[str, str]] = []
    out_of_room: list[dict[str, str]] = []

    for recipient in recipients:
        item = {
            "ad_login": recipient.ad_login,
            "ktalk_mention_id": recipient.ktalk_mention_id,
            "ad_name": recipient.ad_name,
        }
        if recipient.ktalk_mention_id in room_members:
            in_room.append(item)
        else:
            out_of_room.append(item)

    logger.info(
        "Recipients split total=%s in_room=%s out_of_room=%s",
        len(recipients),
        len(in_room),
        len(out_of_room),
    )
    return in_room, out_of_room


def process_open_event(payload: EventPayload) -> None:
    logger.debug("Start process_open_event for insight_id=%s", payload.insight_id)

    jira_data = get_jira_data(payload.insight_id)
    if not jira_data.is_actual:
        logger.info("Service %s is not actual, skip", payload.insight_id)
        return

    active_count = get_active_incident_count(payload.insight_id)
    if active_count > 0:
        counter = update_event_counter(payload.insight_id, delta=1)
        thread_root_event_id = get_last_thread_id(payload.insight_id)
        if thread_root_event_id:
            logger.debug("Active incident counter incremented to=%s, send message to existing thread", counter)
            send_to_ktalk_message(
                payload.message,
                payload.trigger_time,
                cnf.KTALK_ROOM_ID,
                payload.event,
                thread_id=thread_root_event_id,
            )
        return

    short_name = extract_shortname(payload.groups)
    trigger_start = convert_trigger_time(payload.trigger_time)

    jira_result = create_jira_incident(
        payload.insight_id,
        short_name,
        payload.trigger_name,
        issue_data=default_issue_template(),
        function_object_key=jira_data.function_object_key,
        jira_incident_type_key=jira_data.jira_incident_type_key,
    )
    jira_key = str(jira_result.get("key", "")).strip()

    thread_root_event_id = create_discussion(
        jira_data.full_name,
        payload.trigger_name,
        payload.message,
        jira_key,
        payload.trigger_time,
    )

    requested_logins = merge_recipients_with_mandatory(jira_data.recipients)
    resolved_recipients = resolve_recipients_via_api(requested_logins)
    room_members = get_room_members(cnf.KTALK_ROOM_ID)
    in_room, out_of_room = split_recipients_by_room_members(resolved_recipients, room_members)

    mentioned_count = mention_users_in_thread(in_room, cnf.KTALK_ROOM_ID, thread_root_event_id)
    invited_count = invite_missing_users_to_room(out_of_room, cnf.KTALK_ROOM_ID)

    logger.info(
        "Recipients final requested=%s resolved=%s mentioned=%s invited=%s",
        len(requested_logins),
        len(resolved_recipients),
        mentioned_count,
        invited_count,
    )

    create_internal_incident(
        insight_id=payload.insight_id,
        short_name=short_name,
        full_name=jira_data.full_name,
        trigger_name=payload.trigger_name,
        recipients=requested_logins,
        trigger_start_time=trigger_start,
        thread_root_event_id=thread_root_event_id,
        jira_issue_key=jira_key,
    )


def process_close_event(payload: EventPayload) -> None:
    logger.debug("Start process_close_event for insight_id=%s", payload.insight_id)

    thread_root_event_id = get_last_thread_id(payload.insight_id)
    jira_issue_key = get_last_jira_issue_key(payload.insight_id)
    counter = update_event_counter(payload.insight_id, delta=-1)

    if thread_root_event_id:
        send_to_ktalk_message(
            payload.message,
            convert_trigger_time(payload.trigger_time),
            cnf.KTALK_ROOM_ID,
            payload.event,
            thread_id=thread_root_event_id,
        )

    if counter > 0:
        return

    if jira_issue_key:
        is_active = validate_jira_incident_status(jira_issue_key)
        if not is_active:
            logger.info("Jira issue %s is already in a closed status", jira_issue_key)
    else:
        logger.warning("No Jira issue key found for insight_id=%s", payload.insight_id)

    if thread_root_event_id:
        mark_discussion_resolved(thread_root_event_id)
        send_to_ktalk_message(
            "Активные алерты, на которые был создан инцидент, отсутствуют",
            convert_trigger_time(payload.trigger_time),
            cnf.KTALK_ROOM_ID,
            payload.event,
            thread_id=thread_root_event_id,
        )

    close_open_incidents(payload.insight_id)


def main() -> int:
    configure_logging()
    logger.debug("Script started")
    try:
        payload = parse_args()
        logger.info("received event=%s insightId=%s", payload.event, payload.insight_id)

        validate_insight_id(payload.insight_id)

        if payload.event == "1":
            process_open_event(payload)
        else:
            active_count = get_active_incident_count(payload.insight_id)
            if active_count == 0:
                logger.info(
                    "Ignore close-event for insight_id=%s: no active incident record in DB",
                    payload.insight_id,
                )
                return 0
            process_close_event(payload)

        logger.debug("Script finished successfully")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
