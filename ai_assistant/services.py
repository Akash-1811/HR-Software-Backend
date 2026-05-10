"""OpenAI calls, rate limiting, and thin orchestration helpers."""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def is_ai_assistant_enabled() -> bool:
    key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not key.strip():
        return False
    return getattr(settings, "AI_ASSISTANT_ENABLED", False)


def consume_rate_limit_slot(user_id: int) -> bool:
    """Returns True if under limit (and consumes one slot). Window is rolling via cache TTL."""
    lim = int(getattr(settings, "AI_ASSISTANT_RATE_LIMIT_PER_MINUTE", 20))
    window = 60
    key = f"ai_assistant:rl:{user_id}"
    try:
        current = int(cache.get(key, 0))
        if current >= lim:
            return False
        if current == 0:
            cache.set(key, 1, timeout=window)
        else:
            cache.incr(key)
        return True
    except Exception:
        return True


def stream_openai_chat(*, messages: list[dict[str, Any]], model: str) -> Iterator[str]:
    """Yield text deltas from Chat Completions streaming API."""
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    stream = client.chat.completions.create(model=model, messages=messages, stream=True)
    for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice or not choice.delta:
            continue
        piece = choice.delta.content or ""
        if piece:
            yield piece


def log_chat_turn(
    *,
    user_id: int,
    organization_id: int | None,
    conversation_id,
    route: str | None,
    error: str | None,
) -> None:
    payload = {
        "event": "ai_assistant.chat",
        "user_id": user_id,
        "organization_id": organization_id,
        "conversation_id": str(conversation_id),
        "route": (route or "")[:300],
        "error": error,
    }
    if error:
        logger.warning(json.dumps(payload))
    else:
        logger.info(json.dumps(payload))
