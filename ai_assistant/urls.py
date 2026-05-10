from django.urls import path

from ai_assistant import views

urlpatterns = [
    path("chat/stream/", views.chat_stream, name="ai-assistant-chat-stream"),
    path(
        "conversations/<uuid:pk>/messages/",
        views.conversation_messages,
        name="ai-assistant-conversation-messages",
    ),
]
