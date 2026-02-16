from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse

from .models import Announcement, MeetingNote
from .services.notifications import (
    committee_users,
    member_users,
    notify_users,
)


@receiver(post_save, sender=Announcement)
def create_announcement_notification(sender, instance, created, **kwargs):
    if created:
        notify_users(
            recipients=member_users(),
            title="New Announcement",
            message=instance.title,
            link=f"/announcements/{instance.id}/",
            send_email=True,
        )

@receiver(post_save, sender=MeetingNote)
def create_meeting_notification(sender, instance, created, **kwargs):
    if created:
        if instance.file:
            link = instance.file.url
        else:
            link = reverse("meeting-minutes-detail", args=[instance.pk])

        recipients = member_users()
        if instance.audience == MeetingNote.AUDIENCE_COMMITTEE:
            recipients = committee_users()

        notify_users(
            recipients=recipients,
            title="New Meeting Note",
            message=instance.title,
            link=link,
            send_email=True,
        )
