from django.contrib import admin
from .models import (
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
    list_display = ("title", "audience", "created_at")
    search_fields = ("title", "content", "description")
    ordering = ("-created_at",)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "published_at")
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
