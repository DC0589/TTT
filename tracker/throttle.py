from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "unknown")


def hit(key, window):
    """Count one event for key inside a fixed window and return the new total."""
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return 1


def count(key):
    return cache.get(key, 0)


def throttle_post(scope, limit, window):
    """Reject POSTs from one IP once it exceeds limit requests per window seconds."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if settings.THROTTLE_ENABLED and request.method == "POST":
                if hit(f"throttle:{scope}:{client_ip(request)}", window) > limit:
                    return HttpResponse(
                        "Too many attempts. Please wait a few minutes and try again.",
                        status=429,
                    )
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
