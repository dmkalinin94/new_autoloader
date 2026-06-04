#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kontur Talk room members and invite helpers for autoalerter."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

import cnf

logger = logging.getLogger("autoalerter")


@dataclass(slots=True)
class InviteMissingUsersResult:
    invited_count: int
    would_invite_count: int
    dry_run_enabled: bool
    dry_run_users: list[dict[str, str]]


@dataclass(slots=True)
class RoomMembersResult:
    members: set[str]
    ok: bool
    status_code: int | None = None
    error: str | None = None


def _build_bearer_header() -> str:
    token = str(cnf.KTALK_BEARER_TOKEN).strip()
    if not token:
        raise ValueError("KTALK_BEARER_TOKEN is empty")
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _common_headers() -> dict[str, str]:
    return {
        "authorization": _build_bearer_header(),
        "content-type": "application/json",
        "host": str(cnf.KTALK_HOST).strip(),
        "origin": str(cnf.KTALK_TALK_HOST).strip(),
        "talk-host": str(cnf.KTALK_TALK_HOST).strip(),
        "accept": "application/json",
    }


def _request_with_retry(method: str, url: str, **kwargs: object) -> requests.Response | None:
    retries = max(
        int(getattr(cnf, "KTALK_SAFE_REQUEST_RETRIES", getattr(cnf, "KTALK_REQUEST_RETRIES", 3))),
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
            if attempt < retries:
                logger.warning(
                    "KTalk request failed on attempt %s/%s, retry in %s seconds: %s",
                    attempt,
                    retries,
                    retry_delay_seconds,
                    error,
                )
                time.sleep(retry_delay_seconds)
                continue
            logger.error("KTalk request failed after all retries: %s", error)
            return None

        if response.status_code >= 500 and attempt < retries:
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

    return None


def get_room_members(room_id: str) -> RoomMembersResult:
    logger.debug("Loading room members room_id=%s", room_id)

    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    room_path = quote(room_id, safe="!:$")
    members_url = f"{base}/_matrix/client/v3/rooms/{room_path}/members"

    response = _request_with_retry(
        "GET",
        members_url,
        headers=_common_headers(),
    )
    if response is None:
        logger.error("KTalk members request failed after all retries room_id=%s", room_id)
        return RoomMembersResult(
            members=set(),
            ok=False,
            status_code=None,
            error="request_failed_after_retries",
        )
    response.encoding = "utf-8"

    if response.status_code == 403:
        logger.error("KTalk members request forbidden status=%s room_id=%s", response.status_code, room_id)
        return RoomMembersResult(
            members=set(),
            ok=False,
            status_code=response.status_code,
            error="forbidden",
        )

    if not response.ok:
        logger.error("KTalk members request failed status=%s room_id=%s", response.status_code, room_id)
        return RoomMembersResult(
            members=set(),
            ok=False,
            status_code=response.status_code,
            error="http_error",
        )

    try:
        payload = response.json()
    except json.JSONDecodeError:
        logger.error("Failed to parse room members JSON room_id=%s", room_id)
        return RoomMembersResult(
            members=set(),
            ok=False,
            status_code=response.status_code,
            error="invalid_json",
        )

    chunk = payload.get("chunk", [])
    if not isinstance(chunk, list):
        logger.error("Unexpected room members payload format room_id=%s", room_id)
        return RoomMembersResult(
            members=set(),
            ok=False,
            status_code=response.status_code,
            error="invalid_payload",
        )

    members: set[str] = set()

    for item in chunk:
        if not isinstance(item, dict):
            continue
        content = item.get("content", {})
        membership = str(content.get("membership", "")).strip().lower()
        if membership != "join":
            continue
        user_id = item.get("state_key") or item.get("user_id") or content.get("user_id")
        if isinstance(user_id, str) and user_id:
            members.add(user_id)

    logger.debug("Room members loaded count=%s room_id=%s", len(members), room_id)
    return RoomMembersResult(
        members=members,
        ok=True,
        status_code=response.status_code,
        error=None,
    )


def invite_user_to_room(room_id: str, user_id: str) -> bool:
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    room_path = quote(room_id, safe="!:$")
    invite_url = f"{base}/_matrix/client/v3/rooms/{room_path}/invite"

    payload = {"user_id": user_id}

    logger.info("KTalk bearer invite start room_id=%s user_id=%s", room_id, user_id)
    response = _request_with_retry(
        "POST",
        invite_url,
        headers=_common_headers(),
        json=payload,
    )
    if response is None:
        logger.error("KTalk bearer invite failed after all retries room_id=%s user_id=%s", room_id, user_id)
        return False
    response.encoding = "utf-8"

    if not response.ok:
        logger.error(
            "KTalk bearer invite failed room_id=%s user_id=%s status=%s",
            room_id,
            user_id,
            response.status_code,
        )
        return False

    logger.info("KTalk bearer invite success room_id=%s user_id=%s", room_id, user_id)
    return True


def build_dry_run_invite_message(dry_run_users: list[dict[str, str]]) -> str:
    if not dry_run_users:
        return (
            "🧪 DRY-RUN\n\n"
            "Инвайты не отправлены.\n"
            "Пользователей для приглашения нет."
        )

    lines = [
        "🧪 DRY-RUN",
        "",
        "Инвайты не отправлены.",
        "Бот проверил, что следующих пользователей нужно было бы пригласить в комнату:",
    ]
    for user in dry_run_users:
        ad_name = str(user.get("ad_name", "")).strip()
        mention_id = str(user.get("ktalk_mention_id", "")).strip()
        ad_login = str(user.get("ad_login", "")).strip()
        display_name = ad_name or ad_login or mention_id
        lines.append(f"- {display_name} {mention_id}".strip())
    return "\n".join(lines)


def invite_missing_users_to_room(
    recipients: list[dict[str, str]],
    room_id: str,
    dry_run_enabled: bool = False,
) -> InviteMissingUsersResult:
    invited = 0
    dry_run_users: list[dict[str, str]] = []

    for recipient in recipients:
        mention_id = str(recipient.get("ktalk_mention_id", "")).strip()
        ad_login = str(recipient.get("ad_login", "")).strip()
        ad_name = str(recipient.get("ad_name", "")).strip()

        if not mention_id:
            logger.warning("Skip invite: empty mention_id for login=%s", ad_login)
            continue

        if dry_run_enabled:
            dry_run_users.append(
                {
                    "ad_login": ad_login,
                    "ad_name": ad_name,
                    "ktalk_mention_id": mention_id,
                }
            )
            continue

        if invite_user_to_room(room_id, mention_id):
            invited += 1
        else:
            logger.warning("KTalk invite failed room_id=%s login=%s", room_id, ad_login)

    would_invite_count = len(dry_run_users) if dry_run_enabled else invited
    return InviteMissingUsersResult(
        invited_count=invited,
        would_invite_count=would_invite_count,
        dry_run_enabled=dry_run_enabled,
        dry_run_users=dry_run_users,
    )
