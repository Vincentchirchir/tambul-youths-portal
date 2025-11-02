from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.contrib.auth import get_user_model

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import Notification, Announcement, MeetingNote

User = get_user_model()
channel_layer = get_channel_layer()

def broadcast(user, title, message, link):
    print("📢 Broadcasting:", title, "to notifications_group")
    async_to_sync(channel_layer.group_send)(
        "notifications_group",
        {
            "type": "send_notification",
            "content":{
                "title": title,
                "message": message,
                "link": link,
            }
        }
    )

@receiver(post_save, sender=Announcement)
def create_announcement_notification(sender, instance, created, **kwargs):
    if created:
        for user in User.objects.all():
            notif = Notification.objects.create(
                recipient=user,
                title="New Announcement",
                message=instance.title,
                link=f"/announcements/{instance.id}/"
            )
            broadcast(user, notif.title, notif.message, notif.link)

@receiver(post_save, sender=MeetingNote)
def create_meeting_notification(sender, instance, created, **kwargs):
    if created:
        if instance.file:
            link = instance.file.url
        else:
            link = reverse("meeting-minutes-detail", args=[instance.pk])

        for user in User.objects.all():
            notif = Notification.objects.create(
                recipient=user,
                title="New Meeting Note",
                message=instance.title,
                link=link,
            )
            broadcast(user, notif.title, notif.message, notif.link)
