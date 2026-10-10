import logging
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ..context_processors import CELEBRATION_DAYS
from ..forms import (
    PlacedStudentForm,
    SelectionForm,
)
from ..models import (
    PlacedStudent,
    Selection,
)
from ..placed_import import parse_placements, read_csv_rows, read_xlsx_rows

logger = logging.getLogger(__name__)

from ..permissions import admin_required, staff_required
from ..services.placements import import_placements, top_companies


@admin_required
@require_GET
def admin_placed_students(request):
    page_obj = Paginator(PlacedStudent.objects.all(), 50).get_page(request.GET.get("page"))
    return render(request, "tracker/admin/placed_students.html", {
        "page_obj": page_obj, "total": page_obj.paginator.count,
        "top_companies": top_companies(),
    })


@admin_required
@require_POST
def admin_placed_import(request):
    upload = request.FILES.get("file")
    if upload is None or upload.size > 2_000_000:
        messages.error(request, "Choose an .xlsx or .csv file smaller than 2 MB.")
        return redirect("admin_placed_students")
    try:
        if upload.name.lower().endswith(".csv"):
            rows = read_csv_rows(upload)
        else:
            rows = read_xlsx_rows(upload)
    except Exception:
        messages.error(request, "We could not read that file. Upload a valid .xlsx or UTF-8 .csv file.")
        return redirect("admin_placed_students")
    entries, error = parse_placements(rows)
    if error:
        messages.error(request, error)
        return redirect("admin_placed_students")
    created, skipped = import_placements(entries)
    messages.success(request, f"Imported {created} placement(s); skipped {skipped} duplicate row(s).")
    return redirect("admin_placed_students")


@admin_required
@require_http_methods(["GET", "POST"])
def admin_placed_edit(request, pk):
    placed = get_object_or_404(PlacedStudent, pk=pk)
    form = PlacedStudentForm(request.POST or None, instance=placed)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Placement updated.")
        return redirect("admin_placed_students")
    return render(request, "tracker/admin/placed_student_form.html", {"form": form})


@admin_required
@require_POST
def admin_placed_delete(request, pk):
    get_object_or_404(PlacedStudent, pk=pk).delete()
    messages.success(request, "Placement removed.")
    return redirect("admin_placed_students")


@staff_required
@require_http_methods(["GET", "POST"])
def staff_selections(request):
    form = SelectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        selection = form.save(commit=False)
        selection.added_by = request.user
        selection.save()
        messages.success(request, "Selection added. It will be celebrated in the banner for 7 days.")
        return redirect("staff_selections")
    page_obj = Paginator(
        Selection.objects.select_related("student"), 25
    ).get_page(request.GET.get("page"))
    return render(request, "tracker/staff/selections.html", {
        "form": form, "page_obj": page_obj,
        "celebration_days": CELEBRATION_DAYS,
        "cutoff": timezone.now() - timedelta(days=CELEBRATION_DAYS),
    })


@staff_required
@require_POST
def staff_selection_delete(request, pk):
    get_object_or_404(Selection, pk=pk).delete()
    messages.success(request, "Selection removed.")
    return redirect("staff_selections")
