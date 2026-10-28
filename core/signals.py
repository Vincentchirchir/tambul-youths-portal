from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.db import transaction

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
        def deliver():
            # Read current audience after commit; never store an expiring storage URL.
            note = MeetingNote.objects.get(pk=instance.pk)
            recipients = committee_users() if note.audience == MeetingNote.AUDIENCE_COMMITTEE else member_users()
            notify_users(
                recipients=recipients,
                title="New Meeting Note",
                message=note.title,
                link=reverse("meeting-minutes-detail", args=[note.pk]),
                send_email=True,
            )
        transaction.on_commit(deliver, robust=True)
