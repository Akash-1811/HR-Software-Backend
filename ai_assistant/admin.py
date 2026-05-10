from django.contrib import admin

from ai_assistant.models import AssistantConversation, AssistantMessage


class AssistantMessageInline(admin.TabularInline):
    model = AssistantMessage
    extra = 0
    readonly_fields = ("role", "content", "created_at")
    can_delete = False


@admin.register(AssistantConversation)
class AssistantConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "organization_id", "updated_at")
    list_filter = ("organization_id",)
    readonly_fields = ("id", "user", "organization_id", "created_at", "updated_at")
    inlines = [AssistantMessageInline]


@admin.register(AssistantMessage)
class AssistantMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "role", "created_at")
    readonly_fields = ("conversation", "role", "content", "created_at")
