"""Lightweight request validation — kept explicit instead of DRF stacks."""

from __future__ import annotations

import re
import uuid
from typing import Any


def parse_chat_payload(body: dict[str, Any], *, max_message_chars: int) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """
    Returns (conversation_id_str_or_none, client_context_dict, error_message).
    conversation_id None → server creates a conversation.
    """
    raw_msg = body.get("message")
    if raw_msg is None:
        return None, None, "message is required"
    if not isinstance(raw_msg, str):
        return None, None, "message must be a string"
    msg = raw_msg.strip()
    if not msg:
        return None, None, "message is empty"
    if len(msg) > max_message_chars:
        return None, None, f"message exceeds {max_message_chars} characters"

    conv_id = body.get("conversation_id")
    conv_str: str | None
    if conv_id is None or conv_id == "":
        conv_str = None
    else:
        if not isinstance(conv_id, str):
            return None, None, "conversation_id must be a string UUID"
        try:
            uuid.UUID(conv_id)
        except ValueError:
            return None, None, "conversation_id must be a valid UUID"
        conv_str = conv_id

    ctx = body.get("client_context")
    client_ctx: dict[str, Any] = {}
    if isinstance(ctx, dict):
        route = ctx.get("route")
        if isinstance(route, str):
            safe = route.strip()[:300]
            if re.match(r"^[/a-zA-Z0-9\-_?=&.%]*$", safe):
                client_ctx["route"] = safe

    return conv_str, client_ctx, None
