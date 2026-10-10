import calendar as pycalendar
import logging
from datetime import date, time, timedelta

from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from ..models import (
    Group,
    Interview,
    InterviewRound,
)

logger = logging.getLogger(__name__)

from ..permissions import staff_required, student_required

BATCH_COLORS = ["#4f46e5", "#0891b2", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0d9488", "#dc2626"]


STATUS_LABELS = {
    "scheduled": "Scheduled", "attended": "Attended", "selected": "Selected",
    "rejected": "Not selected", "no_show": "No-show", "rescheduled": "Rescheduled",
}


def _batch_color(group_id):
    return BATCH_COLORS[group_id % len(BATCH_COLORS)]


def _interview_event(iv, url, title):
    status = iv.calendar_status
    return {
        "date": iv.date_of_interview, "kind": "interview", "title": title,
        "detail": iv.role, "time": iv.time_of_interview, "url": url,
        "status": status, "status_label": STATUS_LABELS[status],
        "color": _batch_color(iv.group_id), "batch": iv.group.name,
        "type": iv.get_interview_type_display(),
    }


def _schedule_events(user, start, end):
    events = []
    interviews = user.interviews.filter(date_of_interview__range=(start, end)).select_related("group", "status")
    for iv in interviews:
        events.append(_interview_event(
            iv, reverse("student_interview_detail", args=[iv.pk]), f"{iv.company_name} interview"))
    rounds = InterviewRound.objects.filter(
        interview__student=user, scheduled_date__range=(start, end),
    ).select_related("interview__group")
    for rnd in rounds:
        iv = rnd.interview
        if (rnd.round_number == 1 and rnd.scheduled_date == iv.date_of_interview
                and rnd.scheduled_time == iv.time_of_interview):
            continue
        events.append({
            "date": rnd.scheduled_date, "kind": "round", "status": "round", "status_label": "Round",
            "title": f"{rnd.interview.company_name}: {rnd.description}",
            "detail": f"Round {rnd.round_number} - {rnd.get_status_display()}",
            "time": rnd.scheduled_time,
            "url": reverse("student_interview_detail", args=[rnd.interview_id]),
            "done": rnd.status != InterviewRound.PENDING,
            "color": _batch_color(rnd.interview.group_id), "batch": rnd.interview.group.name,
        })
    events.sort(key=lambda e: (e["date"], e.get("time") or time.min, e["title"]))
    return events


def _reminders(user, days=7):
    today = timezone.localdate()
    items = []
    for event in _schedule_events(user, today, today + timedelta(days=days)):
        if event.get("done"):
            continue
        delta = (event["date"] - today).days
        event["when"] = "Today" if delta == 0 else "Tomorrow" if delta == 1 else f"In {delta} days"
        event["urgent"] = delta <= 1
        items.append(event)
    return items


CALENDAR_VIEWS = [("month", "Month"), ("week", "Week"), ("day", "Day"), ("list", "List")]


def _calendar_context(request, fetch_events):
    today = timezone.localdate()
    view = request.GET.get("view", "month")
    if view not in dict(CALENDAR_VIEWS):
        view = "month"
    anchor = None
    try:
        anchor = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        try:
            year, month = (int(part) for part in request.GET.get("month", "").split("-"))
            anchor = date(year, month, 1)
        except (ValueError, TypeError):
            pass
    anchor = anchor or today
    if view in ("month", "list"):
        anchor = anchor.replace(day=1)
        weeks = pycalendar.Calendar(firstweekday=0).monthdatescalendar(anchor.year, anchor.month)
        start, end = weeks[0][0], weeks[-1][-1]
        step_prev = (anchor - timedelta(days=1)).replace(day=1)
        step_next = (anchor + timedelta(days=32)).replace(day=1)
        title = anchor.strftime("%B %Y")
    elif view == "week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
        step_prev, step_next = anchor - timedelta(days=7), anchor + timedelta(days=7)
        title = f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    else:
        start = end = anchor
        step_prev, step_next = anchor - timedelta(days=1), anchor + timedelta(days=1)
        title = anchor.strftime("%A, %d %B %Y")
    events = fetch_events(start, end)
    by_day = {}
    for event in events:
        by_day.setdefault(event["date"], []).append(event)

    def day_cell(day, in_month=True):
        return {"date": day, "in_month": in_month, "today": day == today, "events": by_day.get(day, [])}

    ctx = {
        "cal_view": view, "cal_views": CALENDAR_VIEWS, "cal_title": title,
        "cal_prev": step_prev.isoformat(), "cal_next": step_next.isoformat(),
        "cal_today": today.isoformat(), "cal_anchor": anchor.isoformat(),
        "status_labels": STATUS_LABELS,
    }
    if view in ("month", "list"):
        ctx["grid"] = [[day_cell(day, day.month == anchor.month) for day in week] for week in weeks]
        ctx["agenda"] = [day_cell(day) for day in sorted(by_day) if day.month == anchor.month]
    elif view == "week":
        ctx["week_days"] = [day_cell(start + timedelta(days=i)) for i in range(7)]
    else:
        ctx["day_events"] = by_day.get(anchor, [])
    return ctx


@student_required
@require_GET
def student_calendar(request):
    ctx = _calendar_context(request, lambda start, end: _schedule_events(request.user, start, end))
    ctx.update({
        "reminders": _reminders(request.user, 14), "active_tab": "calendar",
        "legend_batches": [{"name": g.name, "color": _batch_color(g.pk)} for g in request.user.student_groups.all()],
        "extra_qs": "",
    })
    return render(request, "tracker/student/calendar.html", ctx)


@staff_required
@require_GET
def admin_calendar(request):
    owner = request.user.data_owner
    batches = list(Group.objects.filter(admin=owner).order_by("name"))
    selected = next((b for b in batches if str(b.pk) == request.GET.get("batch", "")), None)

    def fetch(start, end):
        qs = Interview.objects.filter(
            group__admin=owner, date_of_interview__range=(start, end),
        ).select_related("student", "group", "status")
        if selected:
            qs = qs.filter(group=selected)
        events = [
            _interview_event(iv, reverse("admin_interview_detail", args=[iv.pk]),
                             f"{iv.student.username} · {iv.company_name}")
            for iv in qs
        ]
        events.sort(key=lambda e: (e["date"], e.get("time") or time.min, e["title"]))
        return events

    ctx = _calendar_context(request, fetch)
    today = timezone.localdate()
    upcoming = Interview.objects.filter(group__admin=owner, date_of_interview__gte=today).select_related(
        "student", "group").order_by("date_of_interview", "time_of_interview", "company_name")
    if selected:
        upcoming = upcoming.filter(group=selected)
    ctx.update({
        "batches": batches, "selected_batch": selected, "upcoming": upcoming[:15],
        "legend_batches": [{"name": g.name, "color": _batch_color(g.pk)} for g in batches],
        "extra_qs": f"&batch={selected.pk}" if selected else "",
    })
    return render(request, "tracker/admin/calendar.html", ctx)
