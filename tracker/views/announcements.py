from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ..forms import AnnouncementForm
from ..models import Announcement
from ..permissions import staff_required


@staff_required
@require_GET
def admin_announcements(request):
    return render(request, "tracker/admin/announcements.html", {"announcements": Announcement.objects.all()[:100]})


@staff_required
@require_http_methods(["GET", "POST"])
def admin_announcement_edit(request, pk=None):
    announcement = get_object_or_404(Announcement, pk=pk) if pk else None
    form = AnnouncementForm(request.POST or None, instance=announcement)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        if announcement is None:
            saved.created_by = request.user
        saved.save()
        messages.success(request, "Announcement saved.")
        return redirect("admin_announcements")
    return render(request, "tracker/admin/announcement_form.html", {"form": form, "announcement": announcement})


@staff_required
@require_POST
def admin_announcement_delete(request, pk):
    get_object_or_404(Announcement, pk=pk).delete()
    messages.success(request, "Announcement deleted.")
    return redirect("admin_announcements")
