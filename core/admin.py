from django.contrib import admin
from .models import Contribution, Loan, Welfare, MeetingNote, Announcement

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
    list_display = ("title", "created_at")
    search_fields = ("title", "body")
    ordering = ("-created_at",)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "published_at")
    search_fields = ("title", "message")
    ordering = ("-published_at",)
