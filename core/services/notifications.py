import logging
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives

from core.models import Notification
from core.permissions import COMMITTEE_ROLES, is_committee_user

logger = logging.getLogger(__name__)

User = get_user_model()
channel_layer = get_channel_layer()
LEGACY_SITE_HOSTS = {"tambulyouths.onrender.com"}
CANONICAL_SITE_BASE_URL = "https://tambul.org"

ALL_NOTIFICATIONS_GROUP = "notifications_all"
COMMITTEE_NOTIFICATIONS_GROUP = "notifications_committee"


def user_notifications_group(user_id: int) -> str:
    return f"notifications_user_{user_id}"


def user_notification_groups(user) -> list[str]:
    groups = [ALL_NOTIFICATIONS_GROUP, user_notifications_group(user.pk)]
    if is_committee_user(user):
        groups.append(COMMITTEE_NOTIFICATIONS_GROUP)
    return groups


def committee_users():
    return User.objects.filter(role__in=COMMITTEE_ROLES, is_active=True)


def member_users():
    return User.objects.filter(is_active=True)


def normalize_notification_link(link: str | None) -> str:
    if not link:
        return ""

    normalized = link.strip()
    if not normalized:
        return ""

    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return normalized

    hostname = (parsed.hostname or "").lower()
    if parsed.scheme in {"http", "https"} and hostname in LEGACY_SITE_HOSTS:
        return urlunsplit(("", "", parsed.path or "/", parsed.query, parsed.fragment))

    return normalized


def _site_base_url() -> str:
    base = getattr(settings, "SITE_BASE_URL", "").strip().rstrip("/")
    try:
        parsed = urlsplit(base)
    except ValueError:
        return base

    hostname = (parsed.hostname or "").lower()
    if parsed.scheme in {"http", "https"} and hostname in LEGACY_SITE_HOSTS:
        return CANONICAL_SITE_BASE_URL

    return base


def _to_absolute_link(link: str | None) -> str:
    normalized_link = normalize_notification_link(link)
    if not normalized_link:
        return ""
    if normalized_link.startswith("http://") or normalized_link.startswith("https://"):
        return normalized_link
    base = _site_base_url()
    separator = "" if normalized_link.startswith("/") else "/"
    return f"{base}{separator}{normalized_link}" if base else normalized_link


def broadcast_realtime(
    title: str,
    message: str,
    link: str | None = None,
    *,
    recipients: Iterable[User] | None = None,
    group_name: str | None = None,
) -> None:
    if channel_layer is None:
        return

    if recipients is not None:
        group_names = {
            user_notifications_group(recipient.pk)
            for recipient in recipients
            if getattr(recipient, "pk", None)
        }
    elif group_name:
        group_names = {group_name}
    else:
        group_names = {ALL_NOTIFICATIONS_GROUP}

    for target_group in group_names:
        async_to_sync(channel_layer.group_send)(
            target_group,
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


def send_email_to_addresses(
    addresses: Iterable[str],
    subject: str,
    message: str,
    link: str | None = None,
) -> int:
    if not getattr(settings, "NOTIFICATIONS_SEND_EMAILS", False):
        return 0

    clean_addresses = []
    seen = set()
    for address in addresses:
        normalized = (address or "").strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        clean_addresses.append(normalized)
        seen.add(key)

    if not clean_addresses:
        return 0

    body = message
    absolute_link = _to_absolute_link(link)
    if absolute_link:
        body = f"{message}\n\nView details: {absolute_link}"

    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=clean_addresses,
        )
        return email.send(fail_silently=False)
    except Exception:
        logger.exception(
            "Failed to send notification email",
            extra={"recipients": clean_addresses, "subject": subject},
        )
        return 0


def notify_users(
    recipients: Iterable[User],
    title: str,
    message: str,
    link: str | None = None,
    send_email: bool = True,
) -> int:
    created = 0
    user_list = list(recipients)
    normalized_link = normalize_notification_link(link)
    for recipient in user_list:
        Notification.objects.create(
            recipient=recipient,
            title=title,
            message=message,
            link=normalized_link,
        )
        created += 1

    broadcast_realtime(
        title=title,
        message=message,
        link=normalized_link,
        recipients=user_list,
    )

    if send_email:
        send_email_notifications(
            recipients=user_list,
            subject=title,
            message=message,
            link=normalized_link,
        )
    return created
