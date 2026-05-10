"""HTTP surface: streaming chat + read-only history."""

from __future__ import annotations

import json
import uuid

from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from Attenova.api_utils import parse_json_request
from django.conf import settings
from Users.auth_utils import require_auth

from ai_assistant.context import build_compact_context
from ai_assistant.models import AssistantConversation, AssistantMessage
from ai_assistant.prompts import SYSTEM_PROMPT, user_context_prefix
from ai_assistant.serializers import parse_chat_payload
from ai_assistant.services import (
    consume_rate_limit_slot,
    is_ai_assistant_enabled,
    log_chat_turn,
    stream_openai_chat,
)


def _sse(data: dict) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode("utf-8")


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def chat_stream(request):
    """
    POST /api/ai-assistant/chat/stream/
    SSE stream: meta → deltas → done | error
    """
    if not is_ai_assistant_enabled():
        return JsonResponse(
            {"error": "AI assistant is disabled or OPENAI_API_KEY is not configured."},
            status=503,
        )

    body, err = parse_json_request(request)
    if err:
        return err

    max_chars = int(getattr(settings, "AI_ASSISTANT_MAX_USER_MESSAGE_CHARS", 6000))
    conv_str, client_ctx, verr = parse_chat_payload(body, max_message_chars=max_chars)
    if verr:
        return JsonResponse({"error": verr}, status=400)

    user = request.user
    if not consume_rate_limit_slot(user.id):
        return JsonResponse({"error": "Rate limit exceeded. Try again shortly."}, status=429)

    msg = body.get("message", "").strip()

    if conv_str:
        conv = AssistantConversation.objects.filter(pk=conv_str, user=user).first()
        if not conv:
            return JsonResponse({"error": "Conversation not found"}, status=404)
    else:
        conv = AssistantConversation.objects.create(
            user=user,
            organization_id=getattr(user, "organization_id", None),
        )

    route = client_ctx.get("route") if client_ctx else None
    compact = build_compact_context(user=user, client_route=route)
    system_msg = SYSTEM_PROMPT + "\n\n" + user_context_prefix(compact)

    prior = list(conv.messages.order_by("-created_at")[:24])
    prior.reverse()
    messages_payload = [{"role": "system", "content": system_msg}]
    for m in prior:
        messages_payload.append({"role": m.role, "content": m.content})
    messages_payload.append({"role": "user", "content": msg})

    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    AssistantMessage.objects.create(conversation=conv, role=AssistantMessage.Role.USER, content=msg)

    def iterator():
        buf: list[str] = []
        try:
            yield _sse({"type": "meta", "conversation_id": str(conv.id)})
            for piece in stream_openai_chat(messages=messages_payload, model=model):
                buf.append(piece)
                yield _sse({"type": "delta", "text": piece})
            full = "".join(buf)
            AssistantMessage.objects.create(
                conversation=conv,
                role=AssistantMessage.Role.ASSISTANT,
                content=full,
            )
            AssistantConversation.objects.filter(pk=conv.pk).update(updated_at=timezone.now())
            log_chat_turn(
                user_id=user.id,
                organization_id=getattr(user, "organization_id", None),
                conversation_id=conv.id,
                route=route,
                error=None,
            )
            yield _sse({"type": "done"})
        except Exception as exc:
            log_chat_turn(
                user_id=user.id,
                organization_id=getattr(user, "organization_id", None),
                conversation_id=conv.id,
                route=route,
                error=str(exc),
            )
            partial = "".join(buf).strip()
            if partial:
                AssistantMessage.objects.create(
                    conversation=conv,
                    role=AssistantMessage.Role.ASSISTANT,
                    content=partial + "\n\n[stream interrupted]",
                )
                AssistantConversation.objects.filter(pk=conv.pk).update(updated_at=timezone.now())
            yield _sse({"type": "error", "message": "Assistant temporarily unavailable."})

    resp = StreamingHttpResponse(iterator(), content_type="text/event-stream; charset=utf-8")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


@csrf_exempt
@require_auth
@require_http_methods(["GET"])
def conversation_messages(request, pk):
    """GET /api/ai-assistant/conversations/<uuid>/messages/"""
    try:
        uuid.UUID(str(pk))
    except ValueError:
        return JsonResponse({"error": "Invalid conversation id"}, status=400)

    conv = AssistantConversation.objects.filter(pk=pk, user=request.user).first()
    if not conv:
        return JsonResponse({"error": "Not found"}, status=404)

    rows = list(conv.messages.order_by("created_at").values("id", "role", "content", "created_at"))
    return JsonResponse({"conversation_id": str(conv.id), "messages": rows}, status=200)
