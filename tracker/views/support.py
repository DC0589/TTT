from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from ..models import SupportThread, User
from ..permissions import staff_required, student_required
from ..services import account_lock, support


def _after(request):
    try:
        return int(request.GET.get("after", 0))
    except ValueError:
        return 0


def _poll(request, thread, as_student):
    messages = list(support.messages_after(thread, _after(request)))
    if messages and messages[-1].from_student != as_student:
        support.mark_seen(thread, as_student)
    return JsonResponse({"messages": support.serialize(messages)})


def _send(request, thread):
    message = support.post(thread, request.user, request.POST.get("body"))
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        if not message:
            return JsonResponse({"error": "Message is empty."}, status=400)
        return JsonResponse({"messages": support.serialize([message])})
    return None


@student_required
def student_support(request):
    thread = support.thread_for(request.user)
    if request.method == "POST":
        response = _send(request, thread)
        return response or redirect("student_support")
    support.mark_seen(thread, as_student=True)
    return render(request, "tracker/support/chat.html", {
        "thread": thread,
        "messages_json": support.serialize(support.messages_after(thread)),
        "send_url": request.path,
        "poll_url": "/support/poll/",
        "as_student": True,
        "peer": "Admin & HR",
    })


@student_required
@require_GET
def student_support_poll(request):
    return _poll(request, support.thread_for(request.user), as_student=True)


@staff_required
@require_GET
def staff_support_inbox(request):
    ids = account_lock.managed_students(request.user).values("pk")
    threads = (SupportThread.objects.filter(student_id__in=ids, last_message_at__isnull=False)
               .select_related("student").order_by("-last_message_at"))
    rows = [{"thread": t, "unread": not t.staff_seen_at or t.staff_seen_at < t.last_message_at,
             "last": t.messages.order_by("-id").first()} for t in threads]
    students = account_lock.managed_students(request.user).order_by("username")
    return render(request, "tracker/support/inbox.html", {"rows": rows, "students": students})


def _staff_thread(request, pk):
    student = get_object_or_404(account_lock.managed_students(request.user), pk=pk)
    return student, support.thread_for(student)


@staff_required
def staff_support_chat(request, pk):
    student, thread = _staff_thread(request, pk)
    if request.method == "POST":
        response = _send(request, thread)
        return response or redirect("staff_support_chat", pk=pk)
    support.mark_seen(thread, as_student=False)
    return render(request, "tracker/support/chat.html", {
        "thread": thread,
        "messages_json": support.serialize(support.messages_after(thread)),
        "send_url": request.path,
        "poll_url": f"/staff/support/{pk}/poll/",
        "as_student": False,
        "peer": student.username,
    })


@staff_required
@require_GET
def staff_support_poll(request, pk):
    _, thread = _staff_thread(request, pk)
    return _poll(request, thread, as_student=False)
