from django.contrib import admin
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from .forms import MeetingNoteForm
from .minutes_storage import MinutesStorageUnavailable
from .models import (
    AIAuditLog,
    CommitteeLetter,
    CommitteeLetterAudit,
    Contribution,
    LetterTemplate,
    Loan,
    Welfare,
    MeetingNote,
    Announcement,
    Notification,
    LoanReminderLog,
    Signatory,
)

@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = ("member", "amount", "created_at")
    search_fields = ("member__email", "member__first_name", "member__last_name")
    list_filter = ("month", "created_at",)

@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("member", "amount", "interest", "status", "created_at")
    search_fields = ("member__email", "member__first_name", "member__last_name")
    list_filter = ("status", "created_at")

@admin.register(Welfare)
class WelfareAdmin(admin.ModelAdmin):
    list_display = ("member", "amount", "status", "date_given")
    search_fields = ("member__email", "member__first_name", "member__last_name", "description")
    list_filter = ("status", "date_given")

@admin.register(MeetingNote)
class MeetingNoteAdmin(admin.ModelAdmin):
    form = MeetingNoteForm
    list_display = ("title", "audience", "created_at")
    search_fields = ("title", "content", "description")
    ordering = ("-created_at",)
    fields = (
        "title", "description", "content", "audience", "minutes_pdf",
        "attachment_download", "legacy_file_name", "posted_by",
    )
    readonly_fields = ("attachment_download", "legacy_file_name", "posted_by")

    @admin.display(description="Attachment")
    def attachment_download(self, obj):
        if obj.pk and (obj.minutes_pdf or obj.file):
            return format_html('<a href="{}">Download attachment</a>', reverse("meeting-minutes-download", args=[obj.pk]))
        return "No attachment"

    @admin.display(description="Preserved legacy filename")
    def legacy_file_name(self, obj):
        return obj.file.name or "—"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.posted_by = request.user
        super().save_model(request, obj, form, change)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except MinutesStorageUnavailable:
            # The admin's transaction rolls back before this handler runs.
            self.message_user(request, "Document storage is temporarily unavailable. No meeting changes were saved.", level=messages.ERROR)
            return redirect(request.path)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "announcement_date", "published_at")
    list_filter = ("announcement_date", "published_at")
    search_fields = ("title", "message")
    ordering = ("-published_at",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "is_read", "created_at")
    list_filter = ("is_read", "created_at")
    search_fields = ("title", "message", "recipient__email", "recipient__first_name", "recipient__last_name")
    ordering = ("-created_at",)


@admin.register(LoanReminderLog)
class LoanReminderLogAdmin(admin.ModelAdmin):
    list_display = ("loan", "reminder_type", "reminder_date", "days_offset", "created_at")
    list_filter = ("reminder_type", "reminder_date", "created_at")
    search_fields = ("loan__member__email", "loan__member__username")
    ordering = ("-created_at",)


@admin.register(AIAuditLog)
class AIAuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "request_id",
        "event_type",
        "user",
        "user_role",
        "tool_name",
        "sensitivity",
        "authorization_result",
        "success",
        "error_category",
        "total_tokens",
        "duration_ms",
    )
    list_filter = (
        "event_type",
        "user_role",
        "sensitivity",
        "authorization_result",
        "success",
        "error_category",
        "created_at",
    )
    search_fields = (
        "request_id",
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
        "tool_name",
        "provider_response_id",
    )
    readonly_fields = (
        "request_id",
        "event_type",
        "user",
        "user_role",
        "tool_name",
        "sensitivity",
        "authorization_result",
        "success",
        "error_category",
        "provider_response_id",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "duration_ms",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LetterTemplate)
class LetterTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "letter_type", "is_active", "created_at")
    list_filter = ("letter_type", "is_active")
    search_fields = ("name", "default_subject", "default_body")


@admin.register(Signatory)
class SignatoryAdmin(admin.ModelAdmin):
    list_display = ("official_name", "position", "user", "is_active")
    list_filter = ("position", "is_active")
    search_fields = ("official_name", "position", "user__username", "user__email")


class CommitteeLetterAuditInline(admin.TabularInline):
    model = CommitteeLetterAudit
    extra = 0
    readonly_fields = (
        "actor",
        "action",
        "status_from",
        "status_to",
        "comment",
        "created_at",
    )
    can_delete = False


@admin.register(CommitteeLetter)
class CommitteeLetterAdmin(admin.ModelAdmin):
    list_display = (
        "reference_number",
        "letter_type",
        "recipient_type",
        "recipient_display_name",
        "status",
        "version",
        "created_by",
        "reviewed_by",
        "approved_by",
        "issued_at",
    )
    list_filter = (
        "letter_type",
        "recipient_type",
        "institution_type",
        "status",
        "letter_date",
        "approved_at",
        "issued_at",
        "created_at",
    )
    search_fields = (
        "reference_number",
        "verification_code",
        "recipient_name",
        "recipient_organization",
        "institution_name",
        "institution_department",
        "attention_name",
        "attention_position",
        "subject",
    )
    readonly_fields = (
        "reference_number",
        "verification_code",
        "created_at",
        "updated_at",
        "approved_at",
    )
    inlines = [CommitteeLetterAuditInline]


@admin.register(CommitteeLetterAudit)
class CommitteeLetterAuditAdmin(admin.ModelAdmin):
    list_display = ("letter", "action", "actor", "status_from", "status_to", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("letter__reference_number", "actor__username", "comment")
    readonly_fields = ("letter", "actor", "action", "status_from", "status_to", "comment", "created_at")
