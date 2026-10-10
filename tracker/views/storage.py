from django.shortcuts import render
from django.views.decorators.http import require_GET

from ..permissions import admin_required
from ..services import storage


@admin_required
@require_GET
def storage_usage(request):
    return render(request, "tracker/admin/storage.html", {"usage": storage.database_usage()})
