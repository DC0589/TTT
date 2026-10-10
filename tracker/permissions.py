from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(flag):
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **k):
            if not getattr(request.user, flag):
                raise PermissionDenied
            return view(request, *a, **k)
        return login_required(wrapper)
    return deco


admin_required = role_required("is_admin")
hr_required = role_required("is_hr")
student_required = role_required("is_student")


def staff_required(view):
    """Allow admins and HR users."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_admin or request.user.is_hr):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return login_required(wrapper)


def dashboard_for(user):
    if user.is_admin:
        return "admin_dashboard"
    if user.is_hr:
        return "hr_students"
    return "student_dashboard"
