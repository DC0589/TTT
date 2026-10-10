from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils import timezone

from .services import attendance, sessions


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


class CheckInGateMiddleware:
    """Students in an active batch must check in for the day before using the rest of the app."""

    EXEMPT = {
        "student_attendance", "student_attendance_mark", "student_leave_apply",
        "student_leave_cancel", "logout", "login",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if settings.CHECKIN_GATE and user.is_authenticated and user.is_student and not request.path.startswith("/static/"):
            try:
                name = resolve(request.path_info).url_name
            except Resolver404:
                name = None
            today = timezone.localdate().isoformat()
            if name not in self.EXEMPT and request.session.get("_checked_in_on") != today:
                if attendance.needs_check_in(user):
                    messages.info(request, "Check in for today to unlock the rest of the app.")
                    return redirect("student_attendance")
                request.session["_checked_in_on"] = today
        return self.get_response(request)
