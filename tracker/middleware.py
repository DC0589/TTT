from .services import sessions


class LoginActivityMiddleware:
    """Keeps the signed-in user's last_seen fresh so the 'online now' list is accurate."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                sessions.heartbeat(request)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Login heartbeat failed")
        return self.get_response(request)
