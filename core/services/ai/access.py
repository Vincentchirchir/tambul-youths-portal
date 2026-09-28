from django.conf import settings


def _normalised_setting_values(name):
    return {
        str(value).strip().lower()
        for value in getattr(settings, name, ())
        if str(value).strip()
    }


def _pilot_allowlist_configured():
    return bool(
        _normalised_setting_values("AI_PILOT_USER_IDS")
        or _normalised_setting_values("AI_PILOT_USERNAMES")
    )


def user_has_ai_pilot_access(user):
    if not getattr(user, "is_authenticated", False):
        return False

    allowed_roles = _normalised_setting_values("AI_PILOT_ROLES")
    if allowed_roles and str(getattr(user, "role", "")).lower() not in allowed_roles:
        return False

    if not _pilot_allowlist_configured():
        return True

    allowed_ids = _normalised_setting_values("AI_PILOT_USER_IDS")
    allowed_usernames = _normalised_setting_values("AI_PILOT_USERNAMES")
    user_id = str(getattr(user, "pk", "")).lower()
    username = str(getattr(user, "username", "")).lower()
    return user_id in allowed_ids or username in allowed_usernames


def can_use_ai_assistant(user):
    return bool(
        getattr(settings, "AI_ENABLED", False)
        and user_has_ai_pilot_access(user)
    )
