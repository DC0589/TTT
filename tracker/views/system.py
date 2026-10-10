import hmac
import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)




def csrf_failure(request, reason=""):
    from django.contrib.auth import logout
    from django.views.csrf import csrf_failure as default_failure
    # A stale token (e.g. after signing in from another tab) must not trap a user on logout.
    if request.path == reverse("logout"):
        logout(request)
        return redirect("login")
    if request.method == "POST" and not request.user.is_authenticated:
        return redirect("login")
    return default_failure(request, reason)


@require_GET
def cron_close_attendance(request):
    secret = settings.CRON_SECRET
    supplied = request.headers.get("Authorization", "")
    if not secret or not hmac.compare_digest(supplied, f"Bearer {secret}"):
        raise PermissionDenied
    from ..services import attendance
    return JsonResponse({"closed": attendance.auto_close_stale()})


@require_GET
def cron_purge_mock_data(request):
    secret = settings.CRON_SECRET
    supplied = request.headers.get("Authorization", "")
    if not secret or not hmac.compare_digest(supplied, f"Bearer {secret}"):
        raise PermissionDenied
    from ..retention import purge_old_mock_data
    return JsonResponse({"purged": purge_old_mock_data()})


@require_GET
def cron_score_mock(request):
    secret = settings.CRON_SECRET
    supplied = request.headers.get("Authorization", "")
    expected = "Bearer " + secret
    if not secret or not hmac.compare_digest(supplied, expected):
        raise PermissionDenied
    from ..services import mock_scoring
    return JsonResponse({"scored": mock_scoring.process_pending(max_items=8, time_budget=45)})
