import logging
from typing import Iterable

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives

from core.models import Notification

logger = logging.getLogger(__name__)

User = get_user_model()
channel_layer = get_channel_layer()

COMMITTEE_ROLES = {
    "chairperson",
    "vice-chairperson",
    "treasurer",
    "secretary",
    "vice-secretary",
    "welfare",
    "coordinator",
    "admin",
    "committee",
}


def committee_users():
    return User.objects.filter(role__in=COMMITTEE_ROLES, is_active=True)


def member_users():
    return User.objects.filter(is_active=True)


def _to_absolute_link(link: str | None) -> str:
    if not link:
        return ""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    base = getattr(settings, "SITE_BASE_URL", "").rstrip("/")
    return f"{base}{link}" if base else link


def broadcast_realtime(title: str, message: str, link: str | None = None) -> None:
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        "notifications_group",
        {
            "type": "send_notification",
            "content": {
                "title": title,
                "message": message,
                "link": link,
            },
        },
    )


def send_email_notifications(
    recipients: Iterable[User],
    subject: str,
    message: str,
    link: str | None = None,
) -> int:
    if not getattr(settings, "NOTIFICATIONS_SEND_EMAILS", False):
        return 0

    sent_count = 0
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    absolute_link = _to_absolute_link(link)

    for recipient in recipients:
        if not recipient.email:
            continue
        body = message
        if absolute_link:
            body = f"{message}\n\nView details: {absolute_link}"
        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=body,
                from_email=from_email,
                to=[recipient.email],
            )
            email.send(fail_silently=False)
            sent_count += 1
        except Exception:
            logger.exception(
                "Failed to send notification email",
                extra={"recipient_id": recipient.pk, "subject": subject},
            )

    return sent_count


def notify_users(
    recipients: Iterable[User],
    title: str,
    message: str,
    link: str | None = None,
    send_email: bool = True,
) -> int:
    created = 0
    user_list = list(recipients)
    for recipient in user_list:
        Notification.objects.create(
            recipient=recipient,
            title=title,
            message=message,
            link=link,
        )
        created += 1

    broadcast_realtime(title=title, message=message, link=link)

    if send_email:
        send_email_notifications(
            recipients=user_list,
            subject=title,
            message=message,
            link=link,
        )
    return created
