from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET

from ..permissions import staff_required
from ..services import sessions as service


@staff_required
@require_GET
def login_activity(request):
    user_id = request.GET.get("user", "")
    page = Paginator(service.history(request.user, int(user_id) if user_id.isdigit() else None), 25)
    online = list(service.online_logs(request.user))
    return render(request, "tracker/staff/login_activity.html", {
        "online": online,
        "online_ids": {log.pk for log in online},
        "page_obj": page.get_page(request.GET.get("page")),
        "users": service.visible_users(request.user).order_by("username"),
        "selected_user": user_id,
        "window_minutes": int(service.ONLINE_WINDOW.total_seconds() // 60),
    })
