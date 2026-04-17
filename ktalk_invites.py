#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kontur Talk room members and invite helpers for autoalerter."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

import requests

import cnf

logger = logging.getLogger("autoalerter")


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


def invite_missing_users_to_room(recipients: list[dict[str, str]], room_id: str) -> int:
    invited = 0

    for recipient in recipients:
        mention_id = str(recipient.get("ktalk_mention_id", "")).strip()
        ad_login = str(recipient.get("ad_login", "")).strip()

        if not mention_id:
            logger.warning("Skip invite: empty mention_id for login=%s", ad_login)
            continue

        if invite_user_to_room(room_id, mention_id):
            invited += 1
        else:
            logger.warning("KTalk invite failed room_id=%s login=%s", room_id, ad_login)

    return invited
