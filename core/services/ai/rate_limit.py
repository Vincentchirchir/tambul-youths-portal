import time

from django.conf import settings
from django.core.cache import cache


def consume_ai_rate_limit(user):
    limit = int(getattr(settings, "AI_RATE_LIMIT", 10))
    window = int(getattr(settings, "AI_RATE_LIMIT_WINDOW_SECONDS", 60))
    if limit <= 0:
        return False, window

    bucket = int(time.time() // window)
    key = f"ai-chat:user:{user.pk}:{bucket}"
    timeout = window + 1

    if cache.add(key, 1, timeout=timeout):
        return True, window

    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=timeout)
        return True, window

    if count > limit:
        return False, window
    return True, window
