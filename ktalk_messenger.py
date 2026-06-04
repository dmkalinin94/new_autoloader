#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kontur Talk messaging helpers for autoalerter."""

from __future__ import annotations

import logging
import time
from html import escape
from typing import Any
from uuid import uuid4
from urllib.parse import quote

import requests

import cnf

logger = logging.getLogger("autoalerter")


def build_ktalk_thread_link(room_id: str, thread_root_event_id: str) -> str:
    """Build KTalk web link for Jira custom field validation."""
    raw_room_id = str(room_id).strip()
    raw_thread_root_event_id = str(thread_root_event_id).strip()

    if not raw_room_id:
        raise ValueError("Cannot build KTalk thread link: room_id is empty")
    if not raw_thread_root_event_id:
        raise ValueError("Cannot build KTalk thread link: thread_root_event_id is empty")

    encoded_room_id = quote(raw_room_id, safe="")
    encoded_thread_root_event_id = quote(raw_thread_root_event_id, safe="")

    template = str(cnf.KTALK_THREAD_WEB_URL_TEMPLATE).strip()
    if not template:
        raise ValueError("KTALK_THREAD_WEB_URL_TEMPLATE is empty")

    thread_link = template.format(
        room_id=encoded_room_id,
        room_id_raw=raw_room_id,
        thread_root_event_id=encoded_thread_root_event_id,
        thread_root_event_id_raw=raw_thread_root_event_id,
        thread_id=encoded_thread_root_event_id,
        thread_id_raw=raw_thread_root_event_id,
    )

    required_prefix = str(getattr(cnf, "KTALK_THREAD_LINK_REQUIRED_PREFIX", "")).strip()
    if required_prefix and required_prefix not in thread_link:
        raise ValueError(
            "KTalk thread link does not contain required Jira validation prefix: "
            f"{required_prefix}"
        )

    return thread_link


def _event_message(event: str, text: str) -> str:
    if event == "1":
        return f"🔴 АВАРИЯ\n\n{text}"
    return f"🟢 ВОССТАНОВЛЕНО\n\n{text}"


def _bot_api_url(endpoint: str) -> str:
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    endpoint = endpoint.lstrip("/")
    return f"{base}/_matrix/client/strangler/api/v1/bot/{cnf.KTALK_JWT_TOKEN}/{endpoint}"


def _safe_bot_endpoint(endpoint: str) -> str:
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    endpoint = endpoint.lstrip("/")
    return f"{base}/_matrix/client/strangler/api/v1/bot/***/{endpoint}"


def _bearer_token() -> str:
    token = str(cnf.KTALK_BEARER_TOKEN).strip()
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _matrix_send_message_url(room_id: str) -> str:
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    room_path = quote(room_id, safe="")
    transaction_id = f"autoalerter-{int(time.time() * 1000)}-{uuid4().hex}"
    return f"{base}/_matrix/client/r0/rooms/{room_path}/send/m.room.message/{transaction_id}"


def _ktalk_json_headers(include_authorization: bool = False) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "host": str(cnf.KTALK_HOST).strip(),
        "origin": str(cnf.KTALK_TALK_HOST).strip(),
        "accept": "application/json",
    }
    if include_authorization:
        headers["authorization"] = _bearer_token()
        headers["talk-host"] = str(cnf.KTALK_TALK_HOST).strip()
    return headers


def _bot_request(
    method: str,
    endpoint: str,
    *,
    retry_on_5xx: bool = True,
    retry_on_network_error: bool = True,
    **kwargs: Any,
) -> requests.Response:
    url = _bot_api_url(endpoint)
    retries_setting_name = (
        "KTALK_SAFE_REQUEST_RETRIES"
        if retry_on_5xx or retry_on_network_error
        else "KTALK_SEND_MESSAGE_RETRIES"
    )
    retries = max(
        int(getattr(cnf, retries_setting_name, getattr(cnf, "KTALK_REQUEST_RETRIES", 3))),
        1,
    )
    retry_delay_seconds = float(getattr(cnf, "KTALK_RETRY_DELAY_SECONDS", 5))

    for attempt in range(1, retries + 1):
        try:
            response = requests.request(
                method,
                url,
                verify=cnf.VERIFY_SSL,
                timeout=cnf.REQUEST_TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as error:
            if retry_on_network_error and attempt < retries:
                logger.warning(
                    "KTalk request failed on attempt %s/%s, retry in %s seconds: %s",
                    attempt,
                    retries,
                    retry_delay_seconds,
                    error,
                )
                time.sleep(retry_delay_seconds)
                continue
            if retry_on_network_error:
                logger.error("KTalk request failed after all retries: %s", error)
            else:
                logger.error("KTalk request failed without retry: %s", error)
            raise

        if response.status_code >= 500 and retry_on_5xx and attempt < retries:
            logger.warning(
                "KTalk request failed on attempt %s/%s with status=%s, retry in %s seconds",
                attempt,
                retries,
                response.status_code,
                retry_delay_seconds,
            )
            time.sleep(retry_delay_seconds)
            continue

        if attempt > 1:
            logger.info("KTalk request succeeded on attempt %s/%s", attempt, retries)
        return response

    raise RuntimeError("KTalk request retry loop ended unexpectedly")


def send_to_ktalk_message(
    text: str,
    trigger_time: str,
    room_id: str,
    event: str,
    thread_id: str | None = None,
    mentions: list[str] | None = None,
    message_format: str = "plain",
    decorate_event: bool = True,
) -> str | None:
    if message_format not in {"plain", "html", "markdown"}:
        raise ValueError(f"Unsupported ktalk message format: {message_format}")

    message_text = _event_message(event, text) if decorate_event else text
    final_text = f"{trigger_time} {message_text}".strip()

    if len(final_text) > 4096:
        logger.error("KTalk message exceeds 4096 chars len=%s", len(final_text))
        return None

    payload = {
        "room_id": room_id,
        "thread_id": thread_id,
        "format": message_format,
        "message": final_text,
        "mentions": mentions or [],
    }

    logger.debug(
        "Kontur Talk Bot API connection details: base_url=%s room_id=%s bot_user=%s endpoint=%s",
        cnf.KTALK_BASE_URL,
        room_id,
        cnf.KTALK_BOT_USER,
        _safe_bot_endpoint("send_message"),
    )

    if mentions:
        message_kind = "mention"
    elif thread_id:
        message_kind = "thread_reply"
    else:
        message_kind = "thread_root"

    try:
        response = _bot_request(
            "POST",
            "send_message",
            retry_on_5xx=False,
            retry_on_network_error=False,
            headers=_ktalk_json_headers(),
            json=payload,
        )
    except requests.RequestException:
        logger.error(
            "KTalk send_message returned ambiguous result; automatic retry disabled to avoid duplicate message "
            "endpoint=send_message room_id=%s thread_id=%s message_kind=%s status_code=%s",
            room_id,
            thread_id,
            message_kind,
            None,
        )
        return None

    if not response.ok:
        status = response.status_code
        if status >= 500:
            logger.error(
                "KTalk send_message returned ambiguous result; automatic retry disabled to avoid duplicate message "
                "endpoint=send_message room_id=%s thread_id=%s message_kind=%s status_code=%s",
                room_id,
                thread_id,
                message_kind,
                status,
            )
        else:
            logger.error("Kontur Talk send failed status=%s", status)
        return None

    try:
        event_id = str(response.json().get("event_id", "")).strip()
    except ValueError:
        event_id = ""

    if not event_id:
        logger.error("Kontur Talk send response has no event_id")
        return None

    logger.debug("Kontur Talk send response status=%s event_id=%s", response.status_code, event_id)
    return event_id


def create_discussion(
    full_name: str,
    trigger_name: str,
    reply: str,
    jira_key: str,
    trigger_time: str,
) -> str | None:
    room_id = cnf.KTALK_ROOM_ID
    jira_issue_url = cnf.JIRA_ISSUE_BROWSE_URL.format(jira_key)

    first_message = (
        f"Сервис: {full_name}\n"
        f"Триггер: {trigger_name}\n"
        f"Инцидент: {jira_key}"
    )
    logger.debug(
        "Using bot=%s fixed Kontur Talk room id=%s for full_name=%r trigger_name=%r",
        cnf.KTALK_BOT_USER,
        room_id,
        full_name,
        trigger_name,
    )

    thread_root_event_id = send_to_ktalk_message(
        first_message,
        "",
        room_id,
        event="1",
        thread_id=None,
    )
    if not thread_root_event_id:
        logger.error(
            "KTalk discussion root was not confirmed; continue incident flow without thread id "
            "room_id=%s jira_key=%s",
            room_id,
            jira_key,
        )
        return None

    thread_message = (
        f"Сервис: {full_name}\n"
        f"Триггер: {trigger_name}\n"
        f"Время события: {trigger_time}\n"
        f"Инцидент Jira: {jira_issue_url}\n\n"
        f"Сообщение мониторинга:\n{reply}"
    )
    thread_reply_event_id = send_to_ktalk_message(
        thread_message,
        "",
        room_id,
        event="1",
        thread_id=thread_root_event_id,
    )
    if not thread_reply_event_id:
        logger.warning("Failed to send first thread reply for event=1 thread_id=%s", thread_root_event_id)

    return thread_root_event_id


def mention_users_in_thread(
    recipients: list[dict[str, str]],
    room_id: str,
    thread_root_event_id: str,
) -> int:
    mentioned = 0

    for recipient in recipients:
        full_name = str(recipient.get("ad_name", "")).strip()
        mention_id = str(recipient.get("ktalk_mention_id", "")).strip()
        ad_login = str(recipient.get("ad_login", "")).strip()

        if not mention_id:
            logger.warning("Skip mention: empty mention_id for login=%s", ad_login)
            continue

        mention_name = full_name or ad_login or mention_id
        mention_body = f"{mention_name} "
        mention_url = (
            f"{cnf.KTALK_TALK_HOST.rstrip('/')}/app/messenger/#/user/"
            f"{quote(mention_id, safe='@:$')}"
        )
        payload: dict[str, Any] = {
            "msgtype": "m.text",
            "body": mention_body,
            "format": "org.matrix.custom.html",
            "formatted_body": f'<a href="{escape(mention_url, quote=True)}">{escape(mention_name)}</a>',
            "m.mentions": {"user_ids": [mention_id]},
            "m.relates_to": {
                "rel_type": "m.thread",
                "event_id": thread_root_event_id,
            },
        }

        try:
            response = requests.request(
                "PUT",
                _matrix_send_message_url(room_id),
                headers=_ktalk_json_headers(include_authorization=True),
                json=payload,
                verify=cnf.VERIFY_SSL,
                timeout=cnf.REQUEST_TIMEOUT,
            )
        except requests.RequestException as error:
            logger.error(
                "KTalk mention returned ambiguous result; automatic retry disabled to avoid duplicate message "
                "endpoint=matrix_send_message room_id=%s thread_id=%s user_id=%s error=%s",
                room_id,
                thread_root_event_id,
                mention_id,
                error,
            )
            continue

        if response.ok:
            mentioned += 1
            continue

        if response.status_code >= 500:
            logger.error(
                "KTalk mention returned ambiguous result; automatic retry disabled to avoid duplicate message "
                "endpoint=matrix_send_message room_id=%s thread_id=%s user_id=%s status_code=%s",
                room_id,
                thread_root_event_id,
                mention_id,
                response.status_code,
            )
        else:
            logger.warning(
                "KTalk mention failed room_id=%s thread_id=%s login=%s status=%s",
                room_id,
                thread_root_event_id,
                ad_login,
                response.status_code,
            )

    return mentioned


def mark_discussion_resolved(thread_root_event_id: str) -> None:
    logger.debug("No room rename operation for Kontur Talk thread_root_event_id=%s", thread_root_event_id)
