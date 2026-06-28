from django.contrib import admin
from .models import Contribution, Loan, LoanPayment, Welfare, MeetingNote, Announcement, Notification, LoanReminderLog

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


@admin.register(LoanPayment)
class LoanPaymentAdmin(admin.ModelAdmin):
    list_display = ("loan", "amount", "payment_date", "recorded_by", "created_at")
    search_fields = ("loan__member__email", "loan__member__first_name", "loan__member__last_name")
    list_filter = ("payment_date", "created_at")

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
