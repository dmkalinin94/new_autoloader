#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kontur Talk room members and invite helpers for autoalerter."""

from __future__ import annotations

import json
import logging
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
        "host": str(cnf.KTALK_HOST).strip(),
        "talk-host": str(cnf.KTALK_TALK_HOST).strip(),
        "accept": "application/json",
    }


def get_room_members(room_id: str) -> set[str]:
    logger.debug("Loading room members room_id=%s", room_id)

    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    room_path = quote(room_id, safe="!:$")
    members_url = f"{base}/_matrix/client/v3/rooms/{room_path}/members"

    response = requests.get(
        members_url,
        headers=_common_headers(),
        verify=cnf.VERIFY_SSL,
        timeout=cnf.REQUEST_TIMEOUT,
    )
    response.encoding = "utf-8"

    if not response.ok:
        logger.error("KTalk members request failed status=%s room_id=%s", response.status_code, room_id)
        return set()

    try:
        payload = response.json()
    except json.JSONDecodeError:
        logger.error("Failed to parse room members JSON room_id=%s", room_id)
        return set()

    members: set[str] = set()

    for item in payload.get("chunk", []):
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
    return members


def invite_user_to_room(room_id: str, user_id: str) -> bool:
    base = str(cnf.KTALK_BASE_URL).rstrip("/")
    room_path = quote(room_id, safe="!:$")
    invite_url = f"{base}/_matrix/client/v3/rooms/{room_path}/invite"

    payload = {"user_id": user_id}

    logger.info("KTalk bearer invite start room_id=%s user_id=%s", room_id, user_id)
    response = requests.post(
        invite_url,
        headers=_common_headers(),
        json=payload,
        verify=cnf.VERIFY_SSL,
        timeout=cnf.REQUEST_TIMEOUT,
    )
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
        return "Dry-run режим инвайтов включен. Пользователей для приглашения нет."

    lines = ["Dry-run режим инвайтов включен. Были бы приглашены:"]
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
