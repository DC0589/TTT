from functools import wraps
import base64
import csv
import io
import hashlib
import hmac
import json
import logging
import re
import secrets
import calendar as pycalendar
from datetime import date, time, timedelta
from decimal import Decimal
from smtplib import SMTPException

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Max, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
    AddMemberForm, FinalStatusForm, GroupForm, HRUserForm, InterviewAdminNotesForm, NoteReplyForm, InterviewForm, InterviewNotesForm,
    LearningCourseForm, MockQuestionForm, RegistrationOTPForm, LeaveRequestForm, PlacedStudentForm, RoundForm, SelectionForm, RoundScheduleForm,
    RoundStatusForm, StudentForm,
    StudentRegistrationForm,
)
from .mock_bank import DIFFICULTY_GUIDE, normalise_difficulty, pick_seed_questions
from .mock_topics import MOCK_TOPICS, pick_focus_areas, topic_language
from .ai_interview import GeminiAPIError, check_health, generate_json
from .models import (
    Attendance, Group, GroupMembership, Interview, InterviewNote, InterviewNoteReply, InterviewRound, InterviewStatus,
    LeaveRequest, LearningCourse, MockInterviewScore, MockInterviewSession, MockQuestion,
    PlacedStudent, Selection, StudentRegistrationRequest, User,
)
from .context_processors import CELEBRATION_DAYS
from .placed_import import parse_placements, read_csv_rows, read_xlsx_rows

logger = logging.getLogger(__name__)
OTP_LIFETIME = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = timedelta(seconds=60)
OTP_MAX_RESENDS = 3
MOCK_QUESTION_COUNT = 10
MOCK_CODING_QUESTION_COUNT = 3


def _registration_code_hash(registration_id, code):
    message = f"{registration_id}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def _send_notification(subject, body, recipients, context, template_name, email_context):
    try:
        message = EmailMultiAlternatives(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            recipients,
        )
        message.attach_alternative(
            render_to_string(template_name, email_context),
            "text/html",
        )
        message.send(fail_silently=False)
    except (OSError, SMTPException, ValueError):
        logger.exception("Email delivery failed: %s", context)
        return False
    return True


def _send_registration_otp(registration, code):
    return _send_notification(
        "Tweak Talent Technologies | Email verification code",
        f"Hello {registration.username},\n\n"
        f"Your email verification code is: {code}\n\n"
        "Enter this code on the email verification page within 10 minutes. "
        f"You have up to {OTP_MAX_ATTEMPTS} attempts. After verification, an administrator "
        "must approve your account request.\n\n"
        "If you did not request this account, you can ignore this email.",
        [registration.email],
        f"student registration {registration.pk}",
        "emails/registration_otp.html",
        {
            "greeting_name": registration.username,
            "headline": "Verify your email address",
            "intro": "Enter this code to confirm that this email address belongs to you.",
            "code": code,
            "expiry_minutes": int(OTP_LIFETIME.total_seconds() // 60),
            "attempt_limit": OTP_MAX_ATTEMPTS,
            "preheader": "Your Tweak Talent Technologies email verification code.",
        },
    )


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


class RoleLoginView(LoginView):
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        from django.urls import reverse
        return reverse(dashboard_for(self.request.user))


def home(request):
    return redirect(dashboard_for(request.user) if request.user.is_authenticated else "login")


def register(request):
    form = StudentRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = timezone.now()
        try:
            with transaction.atomic():
                registration = StudentRegistrationRequest.objects.create(
                    username=form.cleaned_data["username"],
                    email=form.cleaned_data["email"],
                    referred_by=form.cleaned_data["referred_by"],
                    mobile_number=form.cleaned_data["mobile_number"],
                    graduation=form.cleaned_data["graduation"],
                    department=form.cleaned_data["department"],
                    hometown=form.cleaned_data["hometown"],
                    parent_name=form.cleaned_data["parent_name"],
                    parent_mobile_number=form.cleaned_data["parent_mobile_number"],
                    skills=form.cleaned_data["skills"],
                    password_hash=make_password(form.cleaned_data["password1"]),
                    verification_code_hash="",
                    verification_last_sent_at=now,
                    verification_expires_at=now + OTP_LIFETIME,
                )
                registration.verification_code_hash = _registration_code_hash(
                    registration.pk, code
                )
                registration.save(update_fields=["verification_code_hash"])
        except IntegrityError:
            form.add_error(None, "A registration for this username or email is already pending.")
            return render(request, "registration/register.html", {"form": form})
        if not _send_registration_otp(registration, code):
            registration.delete()
            form.add_error(None, "We could not send the verification email. Please try again later.")
            return render(request, "registration/register.html", {"form": form})
        messages.success(request, "Check your email for your 6-digit verification code.")
        if settings.EMAIL_BACKEND.endswith(".console.EmailBackend"):
            messages.info(
                request,
                "Development email mode is active. The verification code is printed in the server console, not sent to your inbox.",
            )
        return redirect("verify_registration", pk=registration.pk)
    return render(request, "registration/register.html", {"form": form})


@require_POST
def resend_registration_otp(request, pk):
    with transaction.atomic():
        registration = get_object_or_404(
            StudentRegistrationRequest.objects.select_for_update(), pk=pk
        )
        if registration.status != StudentRegistrationRequest.AWAITING_VERIFICATION:
            messages.info(request, "This registration is no longer awaiting email verification.")
            return redirect("login")

        now = timezone.now()
        if registration.verification_expires_at <= now:
            registration.status = StudentRegistrationRequest.EXPIRED
            registration.resolved_at = now
            registration.password_hash = ""
            registration.verification_code_hash = ""
            registration.save(update_fields=[
                "status", "resolved_at", "password_hash", "verification_code_hash",
            ])
            messages.error(request, "This verification request expired. Please register again.")
            return redirect("register")

        if registration.verification_resend_count >= OTP_MAX_RESENDS:
            messages.error(request, "You have reached the resend limit. Please register again.")
            return redirect("verify_registration", pk=registration.pk)

        if registration.verification_last_sent_at:
            elapsed = now - registration.verification_last_sent_at
            if elapsed < OTP_RESEND_COOLDOWN:
                wait_seconds = max(1, int((OTP_RESEND_COOLDOWN - elapsed).total_seconds() + 0.99))
                messages.info(request, f"Please wait {wait_seconds} seconds before requesting another code.")
                return redirect("verify_registration", pk=registration.pk)

        code = f"{secrets.randbelow(1_000_000):06d}"
        if not _send_registration_otp(registration, code):
            messages.error(request, "We could not send another email. Check the address or try again later.")
            return redirect("verify_registration", pk=registration.pk)

        registration.verification_code_hash = _registration_code_hash(registration.pk, code)
        registration.verification_attempts = 0
        registration.verification_resend_count += 1
        registration.verification_last_sent_at = now
        registration.verification_expires_at = now + OTP_LIFETIME
        registration.save(update_fields=[
            "verification_code_hash", "verification_attempts", "verification_resend_count",
            "verification_last_sent_at", "verification_expires_at",
        ])

    messages.success(request, "A new verification code was sent. Only the newest code will work.")
    return redirect("verify_registration", pk=registration.pk)


def registration_submitted(request):
    return render(request, "registration/submitted.html", {
        "email_verified": request.GET.get("verified") == "1",
    })


@require_http_methods(["GET", "POST"])
def verify_registration(request, pk):
    with transaction.atomic():
        registration = get_object_or_404(
            StudentRegistrationRequest.objects.select_for_update(), pk=pk
        )
        if registration.status != StudentRegistrationRequest.AWAITING_VERIFICATION:
            messages.info(request, "This registration has already been verified or is no longer active.")
            return redirect("login")
        if registration.verification_expires_at <= timezone.now():
            registration.status = StudentRegistrationRequest.EXPIRED
            registration.resolved_at = timezone.now()
            registration.password_hash = ""
            registration.verification_code_hash = ""
            registration.save(update_fields=[
                "status", "resolved_at", "password_hash", "verification_code_hash",
            ])
            messages.error(request, "This verification code has expired. Please register again.")
            return redirect("register")
        if request.method == "GET":
            if registration.verification_attempts >= OTP_MAX_ATTEMPTS:
                messages.error(request, "Too many incorrect codes. Please register again.")
                return redirect("register")
            return render(request, "registration/verify_email.html", {
                "registration": registration,
                "form": RegistrationOTPForm(),
                "can_resend": registration.verification_resend_count < OTP_MAX_RESENDS,
                "email_backend_console": settings.EMAIL_BACKEND.endswith(
                    ".console.EmailBackend"
                ),
            })

        form = RegistrationOTPForm(request.POST)
        if not form.is_valid():
            return render(request, "registration/verify_email.html", {
                "registration": registration,
                "form": form,
                "can_resend": registration.verification_resend_count < OTP_MAX_RESENDS,
                "email_backend_console": settings.EMAIL_BACKEND.endswith(
                    ".console.EmailBackend"
                ),
            })
        expected_hash = _registration_code_hash(registration.pk, form.cleaned_data["code"])
        if not hmac.compare_digest(registration.verification_code_hash, expected_hash):
            registration.verification_attempts += 1
            if registration.verification_attempts >= OTP_MAX_ATTEMPTS:
                registration.status = StudentRegistrationRequest.EXPIRED
                registration.resolved_at = timezone.now()
                registration.password_hash = ""
                registration.verification_code_hash = ""
                registration.save(update_fields=[
                    "verification_attempts", "status", "resolved_at",
                    "password_hash", "verification_code_hash",
                ])
                messages.error(request, "Too many incorrect codes. Please register again.")
                return redirect("register")
            registration.save(update_fields=["verification_attempts"])
            form.add_error("code", "That code is incorrect. Check your email and try again.")
            return render(request, "registration/verify_email.html", {
                "registration": registration,
                "form": form,
                "can_resend": registration.verification_resend_count < OTP_MAX_RESENDS,
                "email_backend_console": settings.EMAIL_BACKEND.endswith(
                    ".console.EmailBackend"
                ),
            })

        registration.status = StudentRegistrationRequest.AWAITING_APPROVAL
        registration.verified_at = timezone.now()
        registration.verification_code_hash = ""
        registration.save(update_fields=["status", "verified_at", "verification_code_hash"])
    messages.success(request, "Email verified. Your account will be created after an administrator approves your request.")
    return redirect(f"{reverse('registration_submitted')}?verified=1")


# ---------- Admin ----------
@staff_required
def admin_dashboard(request):
    interviews = (Interview.objects.filter(group__admin=request.user)
                  .select_related("student", "group", "status").prefetch_related("rounds"))
    students = User.objects.filter(is_student=True).filter(
        Q(memberships__group__admin=request.user)
        | Q(created_by=request.user)
        | Q(created_by__created_by=request.user)
    ).distinct()
    stats = {
        "students": students.count(),
        "groups": request.user.groups_created.count(),
        "interviews": interviews.count(),
        "selected": interviews.filter(status__final_status="selected").count(),
    }
    outcome_counts = {
        "in_progress": interviews.filter(status__isnull=True).count(),
        "selected": stats["selected"],
        "not_selected": interviews.filter(status__final_status="not-selected").count(),
    }
    decided = outcome_counts["selected"] + outcome_counts["not_selected"]
    outcome_chart = [
        {
            "label": label,
            "count": count,
            "percent": round(count * 100 / max(interviews.count(), 1)),
        }
        for label, count in (
            ("In progress", outcome_counts["in_progress"]),
            ("Selected", outcome_counts["selected"]),
            ("Not selected", outcome_counts["not_selected"]),
        )
    ]
    batches = list(request.user.groups_created.order_by("name"))
    batch_id = request.GET.get("batch", "")
    selected_batch = next((b for b in batches if str(b.pk) == batch_id), None)
    roster = students
    if selected_batch:
        roster = roster.filter(memberships__group=selected_batch)
    roster = roster.annotate(
        mock_count=Count("mock_interview_sessions", filter=Q(mock_interview_sessions__rating__isnull=False), distinct=True),
        mock_avg=Avg("mock_interview_sessions__rating"),
        interview_count=Count("interviews", filter=Q(interviews__group__admin=request.user), distinct=True),
        selected_count=Count("interviews", filter=Q(
            interviews__group__admin=request.user, interviews__status__final_status="selected"), distinct=True),
    ).order_by("username")
    progress_rows = []
    for student in roster[:100]:
        avg = float(student.mock_avg) if student.mock_avg is not None else None
        progress_rows.append({
            "student": student, "mock_count": student.mock_count,
            "mock_avg": round(avg, 2) if avg is not None else None,
            "percent": round(avg / 5 * 100) if avg is not None else 0,
            "weak": avg is not None and avg < 3,
        })
    unseen_replies = list(
        InterviewNoteReply.objects.filter(
            seen_by_admin=False, note__interview__group__admin=request.user)
        .select_related("note__interview__student").order_by("-created_at")[:10])
    return render(request, "tracker/admin/dashboard.html", {
        "unseen_replies": unseen_replies,
        "unseen_reply_count": InterviewNoteReply.objects.filter(
            seen_by_admin=False, note__interview__group__admin=request.user).count(),
        "stats": stats, "interviews": interviews[:20],
        "progress_rows": progress_rows, "batches": batches, "selected_batch": selected_batch,
        "roster_total": roster.count(),
        "outcome_chart": outcome_chart,
        "selection_rate": round(outcome_counts["selected"] * 100 / decided) if decided else 0,
        "pending_registrations": StudentRegistrationRequest.objects.filter(
            status=StudentRegistrationRequest.AWAITING_APPROVAL
        ).count(),
        "upcoming_interviews": interviews.filter(
            date_of_interview__gte=timezone.localdate()
        ).order_by("date_of_interview", "company_name")[:5],
    })


@staff_required
def admin_hr_user_add(request):
    form = HRUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        hr_user = form.save(commit=False)
        hr_user.created_by = request.user
        hr_user.save()
        messages.success(request, f"HR account {hr_user.username} created.")
        return redirect("admin_hr_user_add")
    return render(request, "tracker/admin/hr_user_form.html", {"form": form})


@hr_required
def hr_students(request):
    search = request.GET.get("q", "").strip()[:100]
    students = User.objects.filter(is_student=True, created_by=request.user)
    if request.user.created_by_id:
        students = User.objects.filter(is_student=True).filter(
            Q(created_by=request.user)
            | Q(memberships__group__admin=request.user.created_by)
        ).distinct()
    if search:
        students = students.filter(
            Q(username__icontains=search) | Q(email__icontains=search)
        )
    page_obj = Paginator(students.order_by("username"), 25).get_page(
        request.GET.get("page")
    )
    return render(request, "tracker/hr/students.html", {
        "students": page_obj.object_list,
        "page_obj": page_obj,
        "search": search,
    })


@staff_required
def admin_student_add(request):
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = form.save(commit=False)
        student.created_by = request.user
        student.save()
        messages.success(request, "Student added.")
        if request.user.is_hr:
            return redirect("hr_students")
        return redirect("admin_student_detail", pk=student.pk)
    return render(request, "tracker/admin/student_form.html", {
        "form": form,
        "page_title": "Add a student",
    })


def _filtered_interviews(request):
    owner = request.user.created_by if request.user.is_hr else request.user
    interviews = (
        Interview.objects.filter(group__admin=owner)
        .select_related("student", "group", "status")
        .prefetch_related("rounds")
    )
    params = request.GET
    batches = list(Group.objects.filter(admin=owner).order_by("name"))
    filters = {
        "batch": params.get("batch", "").strip(),
        "company": params.get("company", "").strip()[:100],
        "student": params.get("student", "").strip()[:100],
        "status": params.get("status", "").strip(),
        "type": params.get("type", "").strip(),
        "date_from": params.get("date_from", "").strip(),
        "date_to": params.get("date_to", "").strip(),
    }
    if filters["batch"].isdigit():
        interviews = interviews.filter(group_id=int(filters["batch"]))
    if filters["company"]:
        interviews = interviews.filter(company_name__icontains=filters["company"])
    if filters["student"]:
        interviews = interviews.filter(student__username__icontains=filters["student"])
    if filters["status"] == "in-progress":
        interviews = interviews.filter(status__isnull=True)
    elif filters["status"] in {"selected", "not-selected"}:
        interviews = interviews.filter(status__final_status=filters["status"])
    if filters["type"] in dict(Interview.TYPE_CHOICES):
        interviews = interviews.filter(interview_type=filters["type"])
    for key, lookup in (("date_from", "date_of_interview__gte"), ("date_to", "date_of_interview__lte")):
        if filters[key]:
            try:
                interviews = interviews.filter(**{lookup: date.fromisoformat(filters[key])})
            except ValueError:
                filters[key] = ""
    return interviews, filters, batches


@staff_required
def admin_interviews(request):
    view_mode = request.GET.get("view", "table")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "table"
    interviews, filters, batches = _filtered_interviews(request)
    query = request.GET.copy()
    query.pop("view", None)
    return render(request, "tracker/admin/interviews.html", {
        "interviews": interviews,
        "view_mode": view_mode,
        "filters": filters,
        "batches": batches,
        "type_choices": Interview.TYPE_CHOICES,
        "filter_query": query.urlencode(),
        "has_filters": any(filters.values()),
    })


@staff_required
@require_GET
def admin_interviews_export(request):
    interviews, _filters, _batches = _filtered_interviews(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="interviews.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Date", "Time", "Student", "Batch", "Company", "Role", "Type", "Result", "Attendance",
        "Rounds cleared", "HR name", "HR contact number", "HR email", "Job link",
    ])
    for iv in interviews.order_by("-date_of_interview", "-time_of_interview"):
        writer.writerow([_csv_safe(x) for x in [
            iv.date_of_interview.isoformat(),
            iv.time_of_interview.strftime("%H:%M") if iv.time_of_interview else "",
            iv.student.username, iv.group.name, iv.company_name, iv.role,
            iv.get_interview_type_display(), iv.final_label, iv.get_attendance_display(),
            iv.progress, iv.hr_name, iv.hr_contact_number, iv.hr_email, iv.job_posting_url,
        ]])
    return response


def _apply_quick_action(iv, action, allow_override):
    """Returns an error message, or None when the change was applied."""
    if action == "offer":
        if iv.final_status != "in-progress" and not allow_override:
            return "This interview already has a final result."
        InterviewStatus.objects.update_or_create(
            interview=iv, defaults={"final_status": InterviewStatus.SELECTED})
        iv.rounds.update(status=InterviewRound.CLEARED)
        if not iv.attendance:
            iv.attendance = Interview.ATTENDED
            iv.save(update_fields=["attendance"])
        return None
    if action in dict(Interview.ATTENDANCE_CHOICES):
        iv.attendance = "" if iv.attendance == action else action
        iv.save(update_fields=["attendance"])
        return None
    return "Unknown action."


def _quick_redirect(request, fallback):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(fallback)


@student_required
@require_POST
def interview_quick(request, pk):
    iv = _own_interview(request, pk)
    error = _apply_quick_action(iv, request.POST.get("action", ""), allow_override=False)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Interview updated.")
    return _quick_redirect(request, reverse("student_interviews"))


@staff_required
@require_POST
def admin_interview_quick(request, pk):
    iv = get_object_or_404(Interview, pk=pk, group__admin=request.user)
    error = _apply_quick_action(iv, request.POST.get("action", ""), allow_override=True)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Interview updated.")
    return _quick_redirect(request, reverse("admin_interviews"))


@staff_required
@require_POST
def admin_interview_notes(request, pk):
    iv = get_object_or_404(Interview, pk=pk, group__admin=request.user)
    form = InterviewAdminNotesForm(request.POST)
    if form.is_valid():
        note = form.save(commit=False)
        note.interview = iv
        note.author = request.user
        note.save()
        messages.success(request, "Trainer note saved.")
    else:
        messages.error(request, "Write a note before saving.")
    return redirect("admin_interview_detail", pk=pk)


@admin_required
def admin_courses(request):
    return render(request, "tracker/admin/courses.html", {
        "courses": LearningCourse.objects.all(),
    })


@admin_required
def admin_course_add(request):
    form = LearningCourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        course = form.save()
        messages.success(request, "Course content created.")
        return redirect("admin_course_edit", pk=course.pk)
    return render(request, "tracker/admin/course_edit.html", {
        "form": form,
        "page_title": "Add a course",
        "submit_label": "Create course",
    })


@admin_required
def admin_course_edit(request, pk):
    course = get_object_or_404(LearningCourse, pk=pk)
    form = LearningCourseForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{course.name} course content updated.")
        return redirect("admin_course_edit", pk=course.pk)
    return render(request, "tracker/admin/course_edit.html", {
        "course": course,
        "form": form,
        "page_title": f"Edit {course.name}",
        "submit_label": "Save course content",
    })


@admin_required
def admin_course_preview(request, pk):
    return render(request, "tracker/admin/course_preview.html", {
        "course": get_object_or_404(LearningCourse, pk=pk),
    })


@admin_required
def admin_reports(request):
    interviews = (
        Interview.objects.filter(
            group__admin=request.user,
            status__final_status__in=(
                InterviewStatus.SELECTED,
                InterviewStatus.NOT_SELECTED,
            ),
        )
        .select_related("student", "group", "status")
        .order_by("status__final_status", "company_name", "student__username")
    )
    return render(request, "tracker/admin/reports.html", {
        "reports": [
            (
                InterviewStatus.SELECTED,
                "Selected",
                interviews.filter(status__final_status=InterviewStatus.SELECTED),
            ),
            (
                InterviewStatus.NOT_SELECTED,
                "Not selected",
                interviews.filter(status__final_status=InterviewStatus.NOT_SELECTED),
            ),
        ],
    })


def _mock_review_filters(request):
    owner = request.user.created_by if request.user.is_hr else request.user
    groups = owner.groups_created.order_by("name")
    batch_value = request.GET.get("batch", "")
    selected_group = groups.filter(pk=int(batch_value)).first() if batch_value.isdigit() else None

    students = User.objects.filter(is_student=True).filter(
        Q(created_by=owner)
        | Q(created_by__created_by=owner)
        | Q(memberships__group__admin=owner)
    )
    if selected_group:
        students = students.filter(memberships__group=selected_group)
    students = students.distinct().order_by("username")
    student_value = request.GET.get("student", "")
    selected_student = students.filter(pk=int(student_value)).first() if student_value.isdigit() else None

    sessions = MockInterviewSession.objects.filter(
        student__is_student=True,
    ).filter(
        Q(student__created_by=owner)
        | Q(student__created_by__created_by=owner)
        | Q(student__memberships__group__admin=owner)
    ).select_related("student").prefetch_related("scores").distinct()
    if selected_group:
        sessions = sessions.filter(student__memberships__group=selected_group)
    if selected_student:
        sessions = sessions.filter(student=selected_student)

    topics = list(sessions.order_by().values_list("role", flat=True).distinct().order_by("role"))
    selected_topic = request.GET.get("topic", "")
    if selected_topic in topics:
        sessions = sessions.filter(role=selected_topic)
    else:
        selected_topic = ""

    from_date_value = request.GET.get("from_date", "")
    to_date_value = request.GET.get("to_date", "")
    from_date = parse_date(from_date_value)
    to_date = parse_date(to_date_value)
    if from_date:
        sessions = sessions.filter(created_at__date__gte=from_date)
    if to_date:
        sessions = sessions.filter(created_at__date__lte=to_date)

    return {
        "owner": owner, "groups": groups, "students": students, "sessions": sessions,
        "topics": topics, "selected_group": selected_group, "selected_student": selected_student,
        "selected_topic": selected_topic, "from_date_value": from_date_value, "to_date_value": to_date_value,
    }


@staff_required
@require_GET
def admin_mock_interviews(request):
    f = _mock_review_filters(request)
    groups, students, sessions, topics = f["groups"], f["students"], f["sessions"], f["topics"]
    selected_group, selected_student, selected_topic = f["selected_group"], f["selected_student"], f["selected_topic"]
    from_date_value, to_date_value = f["from_date_value"], f["to_date_value"]
    paginator = Paginator(sessions.order_by("-created_at"), 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    def page_url(page_number):
        query = request.GET.copy()
        query["page"] = page_number
        return f"?{query.urlencode()}"

    return render(request, "tracker/admin/mock_interviews.html", {
        "batches": groups,
        "students": students,
        "topics": topics,
        "selected_batch": selected_group,
        "selected_student": selected_student,
        "selected_topic": selected_topic,
        "from_date": from_date_value,
        "to_date": to_date_value,
        "page_obj": page_obj,
        "previous_page_url": page_url(page_obj.previous_page_number()) if page_obj.has_previous() else None,
        "next_page_url": page_url(page_obj.next_page_number()) if page_obj.has_next() else None,
        "result_count": paginator.count,
        "export_query": request.GET.urlencode(),
    })


def _csv_safe(value):
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@staff_required
@require_GET
def admin_mock_interviews_export(request):
    f = _mock_review_filters(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="mock-interview-report.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Date (IST)", "Student", "Topic", "Level", "Rating (out of 5)", "Answers submitted",
        "Answers expected", "Integrity score", "Integrity flags", "Auto-ended", "Flag summary",
    ])
    for session in f["sessions"].order_by("-created_at"):
        counts = {}
        for event in session.integrity_events or []:
            if event.get("type") != "auto_ended":
                counts[event.get("type")] = counts.get(event.get("type"), 0) + 1
        writer.writerow([_csv_safe(x) for x in [
            timezone.localtime(session.created_at).strftime("%Y-%m-%d %H:%M"),
            session.student.username, session.role, session.difficulty or "",
            f"{session.rating:.1f}" if session.rating is not None else "",
            len(session.scores.all()), session.expected_answers,
            session.integrity_score, session.integrity_flag_count,
            "yes" if session.auto_ended else "no",
            "; ".join(f"{k}: {n}" for k, n in sorted(counts.items())),
        ]])
    return response


@staff_required
@require_GET
def admin_mock_interviews_report(request):
    f = _mock_review_filters(request)
    sessions = list(f["sessions"].order_by("student__username", "-created_at")[:300])
    return render(request, "tracker/admin/mock_report.html", {
        "sessions": sessions,
        "student": f["selected_student"],
        "batch": f["selected_group"],
        "topic": f["selected_topic"],
        "from_date": f["from_date_value"],
        "to_date": f["to_date_value"],
        "generated_at": timezone.localtime(),
    })


@staff_required
def admin_interview_detail(request, pk):
    owner = request.user.created_by if request.user.is_hr else request.user
    interview = get_object_or_404(
        Interview.objects.select_related("student", "group", "status").prefetch_related("rounds"),
        pk=pk,
        group__admin=owner,
    )
    trainer_notes = None
    if request.user.is_admin:
        trainer_notes = list(interview.trainer_notes.select_related("author").prefetch_related("replies__author"))
        InterviewNoteReply.objects.filter(note__interview=interview, seen_by_admin=False).update(seen_by_admin=True)
    return render(request, "tracker/admin/interview_detail.html", {
        "iv": interview,
        "notes_form": InterviewAdminNotesForm() if request.user.is_admin else None,
        "trainer_notes": trainer_notes,
    })


@staff_required
def registrations_pending_count(request):
    count = StudentRegistrationRequest.objects.filter(
        status=StudentRegistrationRequest.AWAITING_APPROVAL
    ).count()
    return JsonResponse({"count": count})


@staff_required
def admin_registrations(request):
    registrations = Paginator(
        StudentRegistrationRequest.objects.filter(
            status=StudentRegistrationRequest.AWAITING_APPROVAL
        ).order_by("-verified_at", "-created_at"),
        20,
    ).get_page(request.GET.get("page"))
    return render(request, "tracker/admin/registrations.html", {
        "registrations": registrations.object_list,
        "page_obj": registrations,
        "pending_count": registrations.paginator.count,
    })


@staff_required
@require_POST
def registration_approve(request, pk):
    with transaction.atomic():
        registration = get_object_or_404(
            StudentRegistrationRequest.objects.select_for_update(),
            pk=pk,
        )
        if registration.status != StudentRegistrationRequest.AWAITING_APPROVAL:
            messages.error(request, "Only email-verified requests can be approved.")
            return redirect("admin_registrations")
        if User.objects.filter(
            Q(username__iexact=registration.username) | Q(email__iexact=registration.email)
        ).exists():
            messages.error(
                request,
                "This request conflicts with an existing account. Resolve the duplicate before approving.",
            )
            return redirect("admin_registrations")
        student = User(
            username=registration.username,
            email=registration.email,
            referred_by=registration.referred_by,
            mobile_number=registration.mobile_number,
            graduation=registration.graduation,
            department=registration.department,
            hometown=registration.hometown,
            parent_name=registration.parent_name,
            parent_mobile_number=registration.parent_mobile_number,
            skills=registration.skills,
            is_student=True,
            created_by=request.user,
            password=registration.password_hash,
        )
        try:
            with transaction.atomic():
                student.save(force_insert=True)
        except IntegrityError:
            messages.error(request, "The account could not be created because its username or email is already in use.")
            return redirect("admin_registrations")
        registration.status = StudentRegistrationRequest.APPROVED
        registration.resolved_at = timezone.now()
        registration.password_hash = ""
        registration.save(update_fields=["status", "resolved_at", "password_hash"])
    if not _send_notification(
        "Your student account is approved",
        f"Hello {student.username},\n\nYour student account has been approved. You can now log in at "
        f"{request.build_absolute_uri(reverse('login'))}.",
        [student.email],
        f"approval notification for registration {pk}",
        "emails/registration_approved.html",
        {
            "greeting_name": student.username,
            "headline": "Your student account is approved",
            "intro": "Your request has been reviewed, and your student account is ready. You can now sign in and start tracking your interview journey.",
            "action_label": "Sign in to your account",
            "action_url": request.build_absolute_uri(reverse("login")),
            "preheader": "Your Tweak Talent student account is ready.",
        },
    ):
        messages.warning(request, f"Account created for {student.username}, but the approval email could not be sent.")
    else:
        messages.success(request, f"Account created for {student.username}.")
    return redirect("admin_registrations")


@staff_required
@require_POST
def registration_reject(request, pk):
    with transaction.atomic():
        registration = get_object_or_404(
            StudentRegistrationRequest.objects.select_for_update(), pk=pk
        )
        if registration.status != StudentRegistrationRequest.AWAITING_APPROVAL:
            messages.error(request, "Only email-verified requests awaiting review can be rejected.")
            return redirect("admin_registrations")
        registration.status = StudentRegistrationRequest.REJECTED
        registration.resolved_at = timezone.now()
        registration.password_hash = ""
        registration.verification_code_hash = ""
        registration.save(update_fields=[
            "status", "resolved_at", "password_hash", "verification_code_hash",
        ])
    if not _send_notification(
        "Student account request update",
        f"Hello {registration.username},\n\n"
        "Your request for a student account was not approved. You may contact the administrator if you have questions.",
        [registration.email],
        f"rejection notification for registration {pk}",
        "emails/registration_rejected.html",
        {
            "greeting_name": registration.username,
            "headline": "An update on your student account request",
            "intro": "Thank you for your interest in Tweak Talent Technologies. After reviewing your request, we’re unable to approve the account at this time.",
            "support_note": "If you believe this decision was made in error or would like more information, please contact the administrator.",
            "preheader": "There is an update about your student account request.",
        },
    ):
        messages.warning(request, f"Registration request for {registration.username} rejected, but the notification email could not be sent.")
    else:
        messages.success(request, f"Registration request for {registration.username} rejected.")
    return redirect("admin_registrations")


@admin_required
@require_POST
def student_delete(request, pk):
    student = get_object_or_404(
        User.objects.filter(is_student=True).filter(
            Q(memberships__group__admin=request.user)
            | Q(created_by=request.user)
            | Q(created_by__created_by=request.user)
        ).distinct(),
        pk=pk,
    )
    student.delete()
    messages.success(request, "Student removed.")
    return redirect("admin_dashboard")


@admin_required
def admin_students(request):
    view_mode = request.GET.get("view", "table")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "table"
    search = request.GET.get("q", "").strip()[:100]
    students = User.objects.filter(is_student=True).filter(
        Q(memberships__group__admin=request.user)
        | Q(created_by=request.user)
        | Q(created_by__created_by=request.user)
    ).annotate(
        interview_count=Count(
            "interviews", filter=Q(interviews__group__admin=request.user), distinct=True
        ),
        selected_count=Count(
            "interviews",
            filter=Q(
                interviews__group__admin=request.user,
                interviews__status__final_status="selected",
            ),
            distinct=True,
        ),
        upcoming_count=Count(
            "interviews",
            filter=Q(
                interviews__group__admin=request.user,
                interviews__date_of_interview__gte=timezone.localdate(),
            ),
            distinct=True,
        ),
    ).distinct().prefetch_related(Prefetch(
        "memberships",
        queryset=GroupMembership.objects.filter(
            group__admin=request.user
        ).select_related("group"),
        to_attr="admin_memberships",
    )).order_by("username")
    if search:
        students = students.filter(
            Q(username__icontains=search)
            | Q(email__icontains=search)
            | Q(memberships__group__name__icontains=search, memberships__group__admin=request.user)
        ).distinct()
    page_obj = Paginator(students, 25).get_page(request.GET.get("page"))
    return render(request, "tracker/admin/students.html", {
        "students": page_obj.object_list,
        "page_obj": page_obj,
        "view_mode": view_mode,
        "search": search,
        "total_students": page_obj.paginator.count,
    })


@admin_required
@require_POST
def admin_student_reset_password(request, pk):
    student = get_object_or_404(
        User.objects.filter(is_student=True).filter(
            Q(memberships__group__admin=request.user)
            | Q(created_by=request.user)
            | Q(created_by__created_by=request.user)
        ).distinct(),
        pk=pk,
    )
    temporary_password = secrets.token_urlsafe(9)
    student.set_password(temporary_password)
    student.save(update_fields=["password"])
    messages.success(
        request,
        f"Temporary password for {student.username}: {temporary_password} "
        "- share it securely; it will not be shown again.",
    )
    return redirect("admin_student_detail", pk=student.pk)


@admin_required
def admin_student_detail(request, pk):
    student = get_object_or_404(
        User.objects.filter(is_student=True).filter(
            Q(memberships__group__admin=request.user)
            | Q(created_by=request.user)
            | Q(created_by__created_by=request.user)
        ).distinct().prefetch_related("memberships__group"),
        pk=pk,
    )
    interviews = (Interview.objects.filter(
        student=student, group__admin=request.user
    ).select_related("group", "status").prefetch_related("rounds"))
    placements = [
        {"company": i.company_name, "role": i.role, "date": i.status.updated_at, "source": "Interview"}
        for i in interviews if i.final_status == InterviewStatus.SELECTED
    ] + [
        {"company": x.company, "role": x.role, "date": x.created_at, "source": "Added by staff"}
        for x in student.selections.all()
    ]
    placements.sort(key=lambda p: p["date"], reverse=True)
    return render(request, "tracker/admin/student_detail.html", {
        "student": student,
        "placements": placements,
        "memberships": student.memberships.filter(
            group__admin=request.user).select_related("group"),
        "interviews": interviews,
        "progress": _mock_progress(student),
        "mock_sessions": student.mock_interview_sessions.order_by("-created_at")[:15],
    })


@staff_required
@require_GET
def admin_groups(request):
    view_mode = request.GET.get("view", "table")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "table"
    owner = request.user.created_by if request.user.is_hr else request.user
    groups = Group.objects.filter(admin=owner).annotate(
        member_count=Count("memberships", distinct=True),
        interview_count=Count("interviews", distinct=True),
    ).order_by("name")
    return render(request, "tracker/admin/groups.html", {
        "groups": groups,
        "view_mode": view_mode,
        "can_manage": request.user.is_admin,
        "can_add": True,
    })


@staff_required
def admin_group_add(request):
    form = GroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.admin = request.user.created_by if request.user.is_hr else request.user
        group.save()
        messages.success(request, "Batch created.")
        return redirect("admin_group_detail", pk=group.pk)
    return render(request, "tracker/admin/group_form.html", {
        "form": form,
        "page_title": "Create a batch",
        "submit_label": "Create batch",
    })


@admin_required
def admin_group_edit(request, pk):
    group = get_object_or_404(Group, pk=pk, admin=request.user)
    form = GroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Batch details updated.")
        return redirect("admin_group_detail", pk=group.pk)
    return render(request, "tracker/admin/group_form.html", {
        "form": form,
        "group": group,
        "page_title": f"Edit {group.name}",
        "submit_label": "Save batch",
    })


@staff_required
@require_GET
def admin_group_detail(request, pk):
    owner = request.user.created_by if request.user.is_hr else request.user
    group = get_object_or_404(Group, pk=pk, admin=owner)
    return render(request, "tracker/admin/group_detail.html", {
        "group": group,
        "memberships": group.memberships.select_related("student"),
        "interviews": Interview.objects.filter(
            student__memberships__group=group,
            group__admin=owner,
        ).select_related("student", "group", "status").prefetch_related("rounds"),
        "can_manage": request.user.is_admin,
        "can_add": True,
    })


@staff_required
def admin_group_member_add(request, pk):
    owner = request.user.created_by if request.user.is_hr else request.user
    group = get_object_or_404(Group, pk=pk, admin=owner)
    form = AddMemberForm(request.POST or None, group=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Student added to batch.")
        return redirect("admin_group_detail", pk=pk)
    return render(request, "tracker/admin/member_form.html", {
        "group": group,
        "form": form,
    })


@admin_required
@require_POST
def group_delete(request, pk):
    get_object_or_404(Group, pk=pk, admin=request.user).delete()
    messages.success(request, "Batch deleted.")
    return redirect("admin_groups")


@admin_required
@require_POST
def member_remove(request, pk, student_id):
    group = get_object_or_404(Group, pk=pk, admin=request.user)
    membership = get_object_or_404(GroupMembership, group=group, student_id=student_id)
    membership.delete()
    messages.success(request, "Student removed from batch. Their interview records have been kept.")
    return redirect("admin_group_detail", pk=pk)


# ---------- Student ----------
def _own_interview(request, pk):
    return get_object_or_404(Interview.objects.select_related("group"), pk=pk, student=request.user)


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
def student_notes(request):
    notes = (InterviewNote.objects
             .filter(interview__student=request.user, visible_to_student=True)
             .select_related("interview", "author").prefetch_related("replies__author")
             .order_by("interview__company_name", "-created_at"))
    companies = {}
    for note in notes:
        companies.setdefault(note.interview, []).append(note)
    return render(request, "tracker/student/notes.html", {
        "companies": companies.items(), "total": len(notes),
    })


@admin_required
@require_POST
def admin_note_reply(request, pk):
    note = get_object_or_404(InterviewNote, pk=pk, interview__group__admin=request.user)
    form = NoteReplyForm(request.POST)
    if not note.visible_to_student:
        messages.error(request, "Only notes shown to the student can be replied to.")
    elif form.is_valid():
        InterviewNoteReply.objects.create(
            note=note, author=request.user, text=form.cleaned_data["text"], seen_by_admin=True)
        messages.success(request, "Reply sent.")
    else:
        messages.error(request, "Write a reply before sending.")
    return redirect("admin_interview_detail", pk=note.interview_id)


@student_required
@require_POST
def student_note_reply(request, pk):
    note = get_object_or_404(InterviewNote, pk=pk, interview__student=request.user, visible_to_student=True)
    form = NoteReplyForm(request.POST)
    if form.is_valid():
        InterviewNoteReply.objects.create(note=note, author=request.user, text=form.cleaned_data["text"])
        messages.success(request, "Reply sent to your trainer.")
    else:
        messages.error(request, "Write a reply before sending.")
    return redirect("student_notes")


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
    owner = request.user.created_by if request.user.is_hr else request.user
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


@student_required
@require_POST
def interview_notes(request, pk):
    iv = _own_interview(request, pk)
    form = InterviewNotesForm(request.POST, instance=iv)
    if form.is_valid():
        form.save()
        messages.success(request, "Preparation notes saved.")
    return redirect("student_interview_detail", pk=iv.pk)


@student_required
@require_POST
def round_schedule(request, pk):
    rnd = get_object_or_404(InterviewRound, pk=pk, interview__student=request.user)
    form = RoundScheduleForm(request.POST, instance=rnd)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    form.save()
    return JsonResponse({"scheduled_date": rnd.scheduled_date.isoformat() if rnd.scheduled_date else ""})


@student_required
def student_dashboard(request):
    interviews = request.user.interviews.select_related("group", "status").prefetch_related("rounds")
    return render(request, "tracker/student/dashboard.html", {
        "groups": request.user.student_groups.all(), "interviews": interviews[:5],
        "active_tab": "overview",
        "reminders": _reminders(request.user),
        "total": interviews.count(),
        "selected": interviews.filter(status__final_status="selected").count(),
        "in_progress": interviews.filter(status__isnull=True).count(),
        "upcoming_interviews": interviews.filter(
            date_of_interview__gte=timezone.localdate()
        ).order_by("date_of_interview", "company_name")[:5],
    })


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
def cron_purge_mock_data(request):
    secret = settings.CRON_SECRET
    supplied = request.headers.get("Authorization", "")
    if not secret or not hmac.compare_digest(supplied, f"Bearer {secret}"):
        raise PermissionDenied
    from .retention import purge_old_mock_data
    return JsonResponse({"purged": purge_old_mock_data()})


def _mock_progress(user):
    rated = list(user.mock_interview_sessions.filter(rating__isnull=False).order_by("created_at"))
    topics = {}
    for session in rated:
        topics.setdefault(session.role, []).append(float(session.rating))
    rows = []
    for topic, ratings in topics.items():
        average = sum(ratings) / len(ratings)
        rows.append({
            "topic": topic, "count": len(ratings), "average": round(average, 2),
            "percent": round(average / 5 * 100), "latest": ratings[-1],
            "weak": average < 3,
        })
    rows.sort(key=lambda row: row["average"])
    levels = []
    for level in ("easy", "medium", "hard"):
        values = [float(x.rating) for x in rated if x.difficulty == level]
        if values:
            average = sum(values) / len(values)
            levels.append({"level": level.title(), "average": round(average, 2),
                           "percent": round(average / 5 * 100), "count": len(values)})
    trend = [{"label": x.created_at.strftime("%b %d"), "topic": x.role,
              "rating": float(x.rating), "percent": round(float(x.rating) / 5 * 100)}
             for x in rated[-12:]]
    attempted = {row["topic"].lower() for row in rows}
    suggestion = None
    if rows and rows[0]["weak"]:
        suggestion = {"topic": rows[0]["topic"], "reason": "your lowest average so far"}
    else:
        for topic in MOCK_TOPICS:
            if topic.lower() not in attempted:
                suggestion = {"topic": topic, "reason": "you haven't tried it yet"}
                break
    overall = None
    if rated:
        values = [float(x.rating) for x in rated]
        overall = {
            "sessions": len(values), "average": round(sum(values) / len(values), 2),
            "best": max(values), "first": values[0], "latest": values[-1],
            "change": round(values[-1] - values[0], 2),
        }
    return {"topics": rows, "levels": levels, "trend": trend, "suggestion": suggestion, "overall": overall}


@student_required
@require_GET
def student_mock_interview(request):
    return render(request, "tracker/student/mock_interview.html", {
        "progress": _mock_progress(request.user),
        "ai_url": reverse("student_mock_interview_ai"),
        "health_url": reverse("student_mock_interview_health"),
        "topics": list(MOCK_TOPICS) + sorted(
            set(MockQuestion.objects.filter(is_active=True).values_list("topic", flat=True)) - set(MOCK_TOPICS)
        ),
        "active_tab": "mock_interview",
        "recent_sessions": request.user.mock_interview_sessions.prefetch_related(
            "scores"
        )[:8],
    })


@student_required
def student_mock_interview_health(request):
    result = check_health(force=request.GET.get("refresh") == "1")
    public = {"status": result["status"], "message": result["message"]}
    return JsonResponse(public, status=200 if result["status"] != "down" else 503)


@student_required
@require_POST
def student_mock_interview_ai(request):
    if len(request.body) > 2_000_000:
        return JsonResponse({"error": "The request is too large. Try again."}, status=413)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not isinstance(data, dict) or data.get("consent") is not True:
        return JsonResponse({"error": "Consent is required before AI analysis."}, status=400)

    action = data.get("action")
    session_id = data.get("session_id")
    if action in {"finish", "results", "integrity"}:
        session = MockInterviewSession.objects.filter(
            pk=session_id, student=request.user
        ).first()
        if session is None:
            return JsonResponse({"error": "Interview session not found."}, status=404)
        if action == "integrity":
            events = data.get("events")
            if not isinstance(events, list):
                return JsonResponse({"error": "Invalid events."}, status=400)
            allowed = {"tab_hidden", "window_blur", "paste", "copy", "context_menu", "devtools_key"}
            stored = list(session.integrity_events or [])
            for event in events[:20]:
                if not isinstance(event, dict) or event.get("type") not in allowed:
                    continue
                if len(stored) >= 300:
                    break
                detail = event.get("detail")
                stored.append({
                    "type": event["type"],
                    "at": timezone.localtime().isoformat(timespec="seconds"),
                    "question": event.get("question") if type(event.get("question")) is int else None,
                    "detail": detail[:80] if isinstance(detail, str) else "",
                })
            session.integrity_events = stored
            limit = settings.MOCK_MAX_INTEGRITY_FLAGS
            terminate = bool(limit) and session.completed_at is None and not session.auto_ended \
                and session.integrity_flag_count >= limit
            if terminate:
                session.integrity_events = stored + [{
                    "type": "auto_ended", "at": timezone.localtime().isoformat(timespec="seconds"),
                    "question": None, "detail": f"limit {limit}",
                }]
            session.save(update_fields=["integrity_events"])
            return JsonResponse({
                "ok": True, "flags": session.integrity_flag_count,
                "limit": limit, "terminate": terminate,
            })

        if action == "finish":
            expected_answers = data.get("expected_answers", 0)
            if type(expected_answers) is not int or not 0 <= expected_answers <= MOCK_QUESTION_COUNT:
                return JsonResponse({"error": "Invalid answer count."}, status=400)
            session.expected_answers = max(session.expected_answers, expected_answers)
            if session.completed_at is None:
                session.completed_at = timezone.now()
            session.save(update_fields=["expected_answers", "completed_at"])

        failed_numbers = data.get("failed_question_numbers", [])
        if action == "results" and isinstance(failed_numbers, list):
            for failed_number in failed_numbers:
                if type(failed_number) is not int or not 1 <= failed_number <= MOCK_QUESTION_COUNT:
                    continue
                score, created = MockInterviewScore.objects.get_or_create(
                    session=session,
                    question_number=failed_number,
                    defaults={
                        "status": MockInterviewScore.FAILED,
                        "answer_feedback": "Feedback could not be loaded.",
                    },
                )
                if not created and score.status == MockInterviewScore.PENDING:
                    score.status = MockInterviewScore.FAILED
                    score.answer_feedback = "Feedback could not be loaded."
                    score.save(update_fields=["status", "answer_feedback"])

        if action == "finish":
            return JsonResponse({"session_id": session.pk})

        scores = list(session.scores.values(
            "question_number", "question", "status", "score",
            "answer_feedback", "camera_feedback", "screen_feedback",
        ))
        finished_answers = sum(
            score["status"] in {MockInterviewScore.COMPLETE, MockInterviewScore.FAILED}
            for score in scores
        )
        failed_answers = sum(score["status"] == MockInterviewScore.FAILED for score in scores)
        pending_count = max(0, session.expected_answers - finished_answers)
        return JsonResponse({
            "session_id": session.pk,
            "expected_answers": session.expected_answers,
            "finished_answers": finished_answers,
            "failed_answers": failed_answers,
            "pending_count": pending_count,
            "ready": pending_count == 0,
            "session_rating": float(session.rating) if session.rating is not None else None,
            "scores": scores,
        })

    role = data.get("role", "")
    if action not in {"question", "feedback"} or not isinstance(role, str):
        return JsonResponse({"error": "Invalid interview request."}, status=400)
    role = role.strip()[:120]
    if not role:
        return JsonResponse({"error": "Enter a role for the interview."}, status=400)

    session = None
    if action == "question":
        previous_questions = list(
            MockInterviewScore.objects.filter(
                session__student=request.user, session__role=role,
            ).exclude(question="").order_by("-id").values_list("question", flat=True)[:40]
        )
        difficulty = normalise_difficulty(data.get("difficulty"))
        seeds = pick_seed_questions(role, difficulty, exclude=previous_questions)
        focus_areas = pick_focus_areas(role)
        default_language = topic_language(role)
        prompt = (
            f"Create exactly {MOCK_QUESTION_COUNT} distinct, concise mock interview questions "
            f"on the topic '{role}'. Mix conceptual, scenario-based and practical questions of "
            "and put them in a varied order. "
            f"{DIFFICULTY_GUIDE[difficulty]} Every question must match this difficulty level. "
            f"Exactly {MOCK_CODING_QUESTION_COUNT} of them must be hands-on coding questions that "
            "the candidate answers by writing code; the rest are spoken questions. "
            "Coding questions must test logic implementation: problem solving with loops, "
            "conditions, string/list/dictionary manipulation, algorithms, pattern printing, "
            "step-by-step data transformation, or SQL queries built from clear business logic. "
            "Do not ask for memorised syntax, library trivia or setup/configuration code. Each "
            "must be solvable with plain language features (and small sample data) in a few "
            "minutes, with a clearly stated input and expected output. "
        )
        if focus_areas:
            prompt += f"Draw from these focus areas for this session: {', '.join(focus_areas)}. "
        if seeds:
            prompt += (
                "Real interview questions to include in this session. Use them as questions "
                "(lightly clean the wording, and write any missing sample data for coding ones). "
                "Items starting with [code] are coding questions. Fill the remaining slots with "
                "new questions of the same style and level:\n- "
                + "\n- ".join(seeds) + "\n"
            )
        if previous_questions:
            prompt += (
                "The candidate has already been asked the questions below in earlier sessions. "
                "Do not repeat or lightly reword any of them:\n- "
                + "\n- ".join(question[:150] for question in previous_questions) + "\n"
            )
        prompt += (
            f"Variation token: {secrets.token_hex(4)}. "
            "Return JSON with one field, questions, an array of exactly "
            f"{MOCK_QUESTION_COUNT} objects. Each object has: text (the question), type "
            "('concept' or 'coding'), and for coding questions only: language ('python', 'sql' or "
            f"'pyspark'; prefer '{default_language}' for this topic) and starter (a short starter "
            "code snippet; for SQL include the CREATE TABLE and INSERT statements for small sample "
            "data so the query can be run). Do not include answers or commentary."
        )
        parts = [{"text": prompt}]
    else:
        session = MockInterviewSession.objects.filter(
            pk=session_id, student=request.user
        ).first()
        if session is None:
            return JsonResponse({"error": "Interview session not found."}, status=404)
        question_number = data.get("question_number")
        question = data.get("question", "")
        if type(question_number) is not int or not 1 <= question_number <= MOCK_QUESTION_COUNT:
            return JsonResponse({"error": "Invalid question number."}, status=400)
        if question_number > session.scores.count() + 1:
            return JsonResponse({"error": "Answer the questions in order."}, status=400)
        if not isinstance(question, str) or not question.strip():
            return JsonResponse({"error": "The interview question is missing."}, status=400)
        role = session.role
        if question_number > session.expected_answers:
            session.expected_answers = question_number
            session.save(update_fields=["expected_answers"])
        audio = data.get("audio", "")
        frames = data.get("frames", {})
        code = data.get("code")
        text_answer = data.get("text")
        lite = data.get("lite") is True
        audio_match = None
        audio_bytes = b""
        if code is not None:
            language = data.get("language")
            code_output = data.get("code_output", "")
            if (
                not isinstance(code, str) or not code.strip() or len(code) > 20_000
                or language not in {"python", "sql", "pyspark"}
                or not isinstance(code_output, str)
            ):
                return JsonResponse({"error": "Write your code before submitting."}, status=400)
        elif text_answer is not None:
            if (
                not lite or not isinstance(text_answer, str)
                or not text_answer.strip() or len(text_answer) > 3000
            ):
                return JsonResponse({"error": "Type your answer before sending."}, status=400)
        else:
            audio_match = re.fullmatch(
                r"data:(audio/(?:webm|mp4|ogg|wav|mpeg|mp3|aac|flac|opus|aiff|m4a))"
                r"(?:;codecs=[A-Za-z0-9.-]+)?;base64,([A-Za-z0-9+/=]+)",
                audio,
            ) if isinstance(audio, str) else None
            if not audio_match or len(audio_match.group(2)) > 600_000:
                return JsonResponse({"error": "Record a valid answer up to 60 seconds long."}, status=400)
            try:
                audio_bytes = base64.b64decode(audio_match.group(2), validate=True)
            except ValueError:
                return JsonResponse({"error": "The recorded answer is invalid."}, status=400)
            if not audio_bytes:
                return JsonResponse({"error": "The recording is empty. Record your answer again."}, status=400)
        if not lite and not isinstance(frames, dict):
            return JsonResponse({"error": "Camera and screen snapshots are required."}, status=400)

        if code is not None:
            parts = [{"text": (
                "Evaluate this coding answer from a mock interview. Assess correctness, edge cases, "
                "efficiency, readability and whether the output matches the question. "
                "Keep each feedback field to one concise sentence of at most 20 words. Do not infer "
                "identity, age, gender, race, health, or personality. Use the camera image only for "
                "framing, lighting, and visibility. Use the screen image only for readability and "
                "relevance to the role. Return JSON with integer score from 1 to 5, "
                "and strings answer_feedback, camera_feedback, and screen_feedback.\n"
                f"Topic: {role}\nQuestion {question_number}: {question[:500]}\n"
                f"Language: {language}\nCandidate code:\n{code}\n"
                f"Output from the candidate's last run:\n{code_output[:2000] or '(not run)'}"
            )}]
        elif text_answer is not None:
            parts = [{"text": (
                "Evaluate this typed mock interview answer. Assess relevance, correctness, "
                "completeness, structure and clarity. Keep answer_feedback to one concise sentence "
                "of at most 20 words. Do not infer identity, age, gender, race, health, or personality. "
                "Return JSON with integer score from 1 to 5 and string answer_feedback.\n"
                f"Topic: {role}\nQuestion {question_number}: {question[:500]}\n"
                f"Candidate answer:\n{text_answer}"
            )}]
        else:
            parts = [{"text": (
                "Evaluate this recorded mock interview answer. Transcribe the speech verbatim and assess "
                "relevance, correctness, completeness, structure, clarity, and verbal delivery. "
                "Keep each feedback field to one concise sentence of at most 20 words. Do not judge "
                "accent or infer identity, age, gender, race, health, or personality. Use the camera image "
                "only for framing, lighting, and visibility. Use the screen image only for readability and "
                "relevance to the role. Return JSON with answer_transcript, integer score from 1 to 5, "
                "and strings answer_feedback, camera_feedback, and screen_feedback.\n"
                f"Role: {role}\nQuestion {question_number}: {question[:500]}"
            )}, {
                "inlineData": {"mimeType": audio_match.group(1), "data": audio_match.group(2)},
            }]
        for frame_name, label in (() if lite else (("camera", "Camera snapshot"), ("screen", "Shared-screen snapshot"))):
            frame = frames.get(frame_name, "")
            match = re.fullmatch(
                r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", frame
            ) if isinstance(frame, str) else None
            if not match or len(match.group(1)) > 400_000:
                return JsonResponse({"error": "Camera and screen snapshots are required."}, status=400)
            try:
                image_bytes = base64.b64decode(match.group(1), validate=True)
            except ValueError:
                return JsonResponse({"error": "Invalid image snapshot."}, status=400)
            if not image_bytes.startswith(b"\xff\xd8\xff"):
                return JsonResponse({"error": "Invalid image snapshot."}, status=400)
            parts.extend([
                {"text": label},
                {"inlineData": {"mimeType": "image/jpeg", "data": match.group(1)}},
            ])

        existing_score = MockInterviewScore.objects.filter(
            session=session, question_number=question_number
        ).first()
        if existing_score and existing_score.status in {
            MockInterviewScore.PENDING, MockInterviewScore.COMPLETE,
        }:
            return JsonResponse({"error": "This answer has already been submitted."}, status=409)
        score_record, _ = MockInterviewScore.objects.update_or_create(
            session=session,
            question_number=question_number,
            defaults={
                "question": question[:500],
                "status": MockInterviewScore.PENDING,
                "score": None,
                "answer_transcript": "",
                "answer_feedback": "",
                "camera_feedback": "",
                "screen_feedback": "",
            },
        )

    try:
        result = generate_json(parts)
    except GeminiAPIError as error:
        if action == "feedback":
            score_record.status = MockInterviewScore.FAILED
            score_record.answer_feedback = "Feedback could not be generated. Please try again later."
            score_record.save(update_fields=["status", "answer_feedback"])
        status = 503 if "not configured" in str(error) else 502
        return JsonResponse({"error": str(error)}, status=status)

    if action == "question":
        raw_questions = result.get("questions")
        if not isinstance(raw_questions, list) or len(raw_questions) < MOCK_QUESTION_COUNT:
            return JsonResponse({"error": "The AI did not return ten questions. Please try again."}, status=502)
        questions = []
        for item in raw_questions:
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            text = item["text"].strip()[:500]
            if not text:
                continue
            question = {"text": text, "type": "concept"}
            if item.get("type") == "coding":
                language = item.get("language")
                if language not in {"python", "sql", "pyspark"}:
                    language = topic_language(role)
                starter = item.get("starter")
                question.update({
                    "type": "coding",
                    "language": language,
                    "starter": starter[:2000] if isinstance(starter, str) else "",
                })
            questions.append(question)
            if len(questions) == MOCK_QUESTION_COUNT:
                break
        if len(questions) != MOCK_QUESTION_COUNT:
            return JsonResponse({"error": "The AI returned invalid questions. Please try again."}, status=502)
        session = MockInterviewSession.objects.create(
            student=request.user, role=role, difficulty=difficulty,
        )
        return JsonResponse({"questions": questions, "session_id": session.pk})

    score = result.get("score", 3)
    if type(score) is not int:
        score = 3
    score = max(1, min(score, 5))
    answer_feedback = str(result.get("answer_feedback", "Review your answer and try again."))[:800]
    transcript_value = result.get("answer_transcript", "") if audio_match else ""
    answer_transcript = (
        transcript_value.strip()[:5000]
        if isinstance(transcript_value, str)
        else ""
    )
    if data.get("lite") is True:
        camera_feedback = screen_feedback = ""
    else:
        camera_feedback = str(result.get("camera_feedback", "No camera feedback available."))[:500]
        screen_feedback = str(result.get("screen_feedback", "No screen feedback available."))[:500]
    score_record.status = MockInterviewScore.COMPLETE
    score_record.score = score
    score_record.answer_transcript = answer_transcript
    score_record.answer_feedback = answer_feedback
    score_record.camera_feedback = camera_feedback
    score_record.screen_feedback = screen_feedback
    score_record.save(update_fields=[
        "status", "score", "answer_transcript", "answer_feedback",
        "camera_feedback", "screen_feedback",
    ])
    average_score = session.scores.filter(
        status=MockInterviewScore.COMPLETE, score__isnull=False
    ).aggregate(average=Avg("score"))["average"]
    if average_score is not None:
        session.rating = Decimal(str(average_score)).quantize(Decimal("0.01"))
        session.save(update_fields=["rating"])
    return JsonResponse({"status": MockInterviewScore.COMPLETE, "question_number": question_number})


@student_required
def student_courses(request):
    return render(request, "tracker/student/curriculum.html", {
        "active_tab": "courses",
        "courses": LearningCourse.objects.all(),
        "page_title": "Courses",
        "page_eyebrow": "YOUR LEARNING ROADMAP",
    })


@student_required
def student_interview_questions(request):
    return render(request, "tracker/student/curriculum.html", {
        "active_tab": "questions",
        "courses": LearningCourse.objects.all(),
        "page_title": "Interview questions",
        "page_eyebrow": "PRACTICE FOR YOUR NEXT STEP",
    })


@student_required
@require_GET
def student_interviews(request):
    view_mode = request.GET.get("view", "cards")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "cards"
    interviews = request.user.interviews.select_related(
        "group", "status"
    ).prefetch_related("rounds")
    return render(request, "tracker/student/interviews.html", {
        "interviews": interviews,
        "view_mode": view_mode,
    })


@student_required
def student_interview_add(request):
    form = InterviewForm(request.POST or None, student=request.user)
    if request.method == "POST" and form.is_valid():
        iv = form.save(commit=False)
        iv.student = request.user
        iv.save()
        round_type = form.cleaned_data["first_round_type"]
        InterviewRound.objects.create(
            interview=iv, round_number=1, round_type=round_type,
            description=dict(InterviewRound.TYPE_CHOICES)[round_type],
            scheduled_date=iv.date_of_interview, scheduled_time=iv.time_of_interview)
        messages.success(request, "Interview added. After it happens, update the round result and add your feedback.")
        return redirect("student_interview_detail", pk=iv.pk)
    return render(request, "tracker/student/interview_form.html", {
        "form": form,
        "page_title": "Add an interview",
        "submit_label": "Save interview",
    })


@student_required
def student_interview_detail(request, pk):
    iv = _own_interview(request, pk)
    return render(request, "tracker/student/interview_detail.html", {
        "iv": iv, "round_form": RoundForm(),
        "status_form": FinalStatusForm(instance=getattr(iv, "status", None), interview=iv),
        "round_choices": InterviewRound.STATUS_CHOICES,
        "round_type_choices": InterviewRound.TYPE_CHOICES,
        "editable": False,
        "edit_mode": False,
    })


@student_required
def student_interview_edit(request, pk):
    iv = _own_interview(request, pk)
    form = InterviewForm(
        request.POST or None,
        instance=iv,
        student=request.user,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Interview details updated.")
        return redirect("student_interview_detail", pk=iv.pk)
    return render(request, "tracker/student/interview_form.html", {
        "iv": iv,
        "form": form,
        "page_title": "Edit interview details",
        "submit_label": "Save interview details",
    })


@student_required
def student_interview_progress(request, pk):
    iv = _own_interview(request, pk)
    return render(request, "tracker/student/interview_detail.html", {
        "iv": iv,
        "round_form": RoundForm(),
        "status_form": FinalStatusForm(instance=getattr(iv, "status", None), interview=iv),
        "round_choices": InterviewRound.STATUS_CHOICES,
        "round_type_choices": InterviewRound.TYPE_CHOICES,
        "editable": True,
        "edit_mode": True,
    })


@student_required
@require_POST
def interview_status(request, pk):
    iv = _own_interview(request, pk)
    if iv.final_status != "in-progress" and request.POST.get("edit_mode") != "1":
        messages.error(request, "Open this interview in edit mode to change its final result.")
        return redirect("student_interviews")
    form = FinalStatusForm(request.POST, instance=getattr(iv, "status", None), interview=iv)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.interview = iv
        obj.save()
        rounds = iv.rounds.order_by("round_number")
        if obj.final_status == InterviewStatus.SELECTED:
            rounds.update(status=InterviewRound.CLEARED)
        else:
            last_round = rounds.last()
            if last_round:
                last_round.status = InterviewRound.REJECTED
                last_round.save(update_fields=["status"])
        messages.success(request, "Final result saved.")
        return redirect("student_interviews")
    else:
        for err in form.errors.values():
            messages.error(request, " ".join(err))
    return redirect("student_interview_detail", pk=pk)


@student_required
@require_POST
def interview_delete(request, pk):
    _own_interview(request, pk).delete()
    messages.success(request, "Interview deleted.")
    return redirect("student_interviews")


@student_required
@require_POST
def round_add(request, pk):
    """AJAX: CSRF-protected via the X-CSRFToken header."""
    iv = _own_interview(request, pk)
    if iv.final_status != "in-progress" and request.POST.get("edit_mode") != "1":
        return JsonResponse({"error": "Open this interview in edit mode to change its rounds."}, status=409)
    if iv.rounds.filter(status=InterviewRound.PENDING).exists():
        return JsonResponse({"error": "Update the result of your current round before adding the next one."}, status=400)
    if iv.rounds.filter(status=InterviewRound.REJECTED).exists():
        return JsonResponse({"error": "You were rejected in an earlier round."}, status=400)
    form = RoundForm(request.POST, interview=iv)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    with transaction.atomic():
        nxt = (iv.rounds.aggregate(m=Max("round_number"))["m"] or 0) + 1
        rnd = InterviewRound.objects.create(
            interview=iv, round_number=nxt, description=form.cleaned_data["description"],
            round_type=form.cleaned_data["round_type"],
            scheduled_date=form.cleaned_data.get("scheduled_date") or (iv.date_of_interview if nxt == 1 else None),
            scheduled_time=form.cleaned_data.get("scheduled_time") or (iv.time_of_interview if nxt == 1 else None))
        InterviewStatus.objects.filter(interview=iv).delete()  # new round reopens the interview
    html = render_to_string("tracker/student/_round_row.html", {
        "r": rnd,
        "round_choices": InterviewRound.STATUS_CHOICES,
        "editable": True,
    }, request=request)
    return JsonResponse({"html": html}, status=201)


@student_required
@require_POST
def round_update(request, pk):
    rnd = get_object_or_404(InterviewRound, pk=pk, interview__student=request.user)
    form = RoundStatusForm(request.POST, instance=rnd)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    form.save()
    if rnd.interview.rounds.filter(status=InterviewRound.PENDING).exists():
        InterviewStatus.objects.filter(interview=rnd.interview).delete()
    return JsonResponse({
        "status": rnd.status,
        "feedback": rnd.feedback,
        "badge": rnd.badge,
        "final_status": rnd.interview.final_status,
        "final_label": rnd.interview.final_label,
    })


@admin_required
@require_GET
def admin_placed_students(request):
    page_obj = Paginator(PlacedStudent.objects.all(), 50).get_page(request.GET.get("page"))
    return render(request, "tracker/admin/placed_students.html", {
        "page_obj": page_obj, "total": page_obj.paginator.count,
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
    existing = {
        (n.lower(), c.lower())
        for n, c in PlacedStudent.objects.values_list("name", "company")
    }
    created = []
    for entry in entries:
        key = (entry["name"].lower(), entry["company"].lower())
        if key in existing:
            continue
        existing.add(key)
        created.append(PlacedStudent(**entry))
    PlacedStudent.objects.bulk_create(created)
    messages.success(
        request,
        f"Imported {len(created)} placement(s); skipped {len(entries) - len(created)} duplicate row(s).",
    )
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


QUESTION_CSV_COLUMNS = ["topic", "difficulty", "type", "text", "language"]


@admin_required
@require_GET
def admin_questions(request):
    questions = MockQuestion.objects.all()
    topic = request.GET.get("topic", "")
    difficulty = request.GET.get("difficulty", "")
    kind = request.GET.get("type", "")
    search = request.GET.get("q", "").strip()
    if topic:
        questions = questions.filter(topic=topic)
    if difficulty in {"easy", "medium", "hard"}:
        questions = questions.filter(difficulty=difficulty)
    if kind in {"concept", "coding"}:
        questions = questions.filter(kind=kind)
    if search:
        questions = questions.filter(text__icontains=search)
    page_obj = Paginator(questions, 25).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "tracker/admin/questions.html", {
        "page_obj": page_obj,
        "topics": MockQuestion.objects.order_by().values_list("topic", flat=True).distinct().order_by("topic"),
        "selected_topic": topic, "selected_difficulty": difficulty,
        "selected_type": kind, "search": search,
        "total": MockQuestion.objects.count(),
        "base_query": query.urlencode(),
    })


def _question_form_page(request, form, title, label):
    return render(request, "tracker/admin/question_form.html", {
        "form": form, "page_title": title, "submit_label": label,
        "topic_suggestions": sorted(
            set(MOCK_TOPICS) | set(MockQuestion.objects.values_list("topic", flat=True))
        ),
    })


@admin_required
@require_http_methods(["GET", "POST"])
def admin_question_add(request):
    form = MockQuestionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Question added to the bank.")
        return redirect("admin_questions")
    return _question_form_page(request, form, "Add a mock interview question", "Add question")


@admin_required
@require_http_methods(["GET", "POST"])
def admin_question_edit(request, pk):
    question = get_object_or_404(MockQuestion, pk=pk)
    form = MockQuestionForm(request.POST or None, instance=question)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Question updated.")
        return redirect("admin_questions")
    return _question_form_page(request, form, "Edit question", "Save changes")


@admin_required
@require_POST
def admin_question_delete(request, pk):
    get_object_or_404(MockQuestion, pk=pk).delete()
    messages.success(request, "Question deleted.")
    return redirect("admin_questions")


@admin_required
@require_GET
def admin_questions_export(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="mock-question-bank.csv"'
    writer = csv.writer(response)
    writer.writerow(QUESTION_CSV_COLUMNS)
    for q in MockQuestion.objects.all():
        writer.writerow([_csv_safe(x) for x in [q.topic, q.difficulty, q.kind, q.text, q.language]])
    return response


@admin_required
@require_POST
def admin_questions_import(request):
    upload = request.FILES.get("file")
    if upload is None or upload.size > 1_000_000:
        messages.error(request, "Choose a CSV file smaller than 1 MB.")
        return redirect("admin_questions")
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        messages.error(request, "The file must be UTF-8 encoded CSV.")
        return redirect("admin_questions")
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not {"topic", "difficulty", "text"} <= headers:
        messages.error(request, "The CSV needs the columns: topic, difficulty, type, text, language.")
        return redirect("admin_questions")
    existing = {
        (t.lower(), d, x.strip().lower())
        for t, d, x in MockQuestion.objects.values_list("topic", "difficulty", "text")
    }
    created, skipped = [], 0
    for index, raw in enumerate(reader):
        if index >= 2000:
            break
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        topic = " ".join(row.get("topic", "").split())[:120]
        difficulty = row.get("difficulty", "").lower() or "medium"
        kind = (row.get("type") or row.get("kind") or "concept").lower()
        language = row.get("language", "").lower()
        body = row.get("text", "")
        if kind == "code":
            kind = "coding"
        if (not topic or not body or difficulty not in {"easy", "medium", "hard"}
                or kind not in {"concept", "coding"} or language not in {"", "python", "sql", "pyspark"}):
            skipped += 1
            continue
        key = (topic.lower(), difficulty, body.lower())
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        created.append(MockQuestion(
            topic=topic, difficulty=difficulty, kind=kind, text=body,
            language=language if kind == "coding" else "",
        ))
    MockQuestion.objects.bulk_create(created)
    messages.success(request, f"Imported {len(created)} question(s); skipped {skipped} duplicate or invalid row(s).")
    return redirect("admin_questions")


def _attendance_owner(user):
    return user.created_by if user.is_hr else user


def _is_active_batch_student(user):
    return user.student_groups.filter(is_active=True).exists()


def _approved_leave_on(student, day):
    return LeaveRequest.objects.filter(
        student=student, status=LeaveRequest.APPROVED,
        start_date__lte=day, end_date__gte=day,
    ).first()


@student_required
@require_GET
def student_attendance(request):
    today = timezone.localdate()
    return render(request, "tracker/student/attendance.html", {
        "eligible": _is_active_batch_student(request.user),
        "today": today,
        "record": Attendance.objects.filter(student=request.user, date=today).first(),
        "leave_today": _approved_leave_on(request.user, today),
        "history": Attendance.objects.filter(student=request.user)[:30],
        "leaves": request.user.leave_requests.all()[:20],
        "leave_form": LeaveRequestForm(student=request.user),
    })


@student_required
@require_POST
def student_attendance_mark(request, action):
    if not _is_active_batch_student(request.user):
        messages.error(request, "Attendance is only for students in an active batch.")
        return redirect("student_attendance")
    now = timezone.now()
    today = timezone.localdate(now)
    if action == "in":
        if _approved_leave_on(request.user, today):
            messages.error(request, "You have approved leave today.")
            return redirect("student_attendance")
        _, created = Attendance.objects.get_or_create(
            student=request.user, date=today, defaults={"check_in": now}
        )
        if created:
            messages.success(request, f"Checked in at {timezone.localtime(now):%I:%M %p}.")
        else:
            messages.info(request, "You have already checked in today.")
    elif action == "out":
        with transaction.atomic():
            record = Attendance.objects.select_for_update().filter(
                student=request.user, date=today
            ).first()
            if record is None:
                messages.error(request, "Check in first.")
            elif record.check_out:
                messages.info(request, "You have already checked out today.")
            else:
                record.check_out = now
                record.save(update_fields=["check_out"])
                messages.success(request, f"Checked out at {timezone.localtime(now):%I:%M %p}.")
    return redirect("student_attendance")


@student_required
@require_POST
def student_leave_apply(request):
    form = LeaveRequestForm(request.POST, student=request.user)
    if form.is_valid():
        leave = form.save(commit=False)
        leave.student = request.user
        leave.save()
        messages.success(request, "Leave request sent. You'll see the decision here.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("student_attendance")


@student_required
@require_POST
def student_leave_cancel(request, pk):
    leave = get_object_or_404(
        LeaveRequest, pk=pk, student=request.user, status=LeaveRequest.PENDING
    )
    leave.status = LeaveRequest.CANCELLED
    leave.save(update_fields=["status"])
    messages.success(request, "Leave request cancelled.")
    return redirect("student_attendance")


def _staff_batches(user):
    return Group.objects.filter(admin=_attendance_owner(user)).order_by("name")


@staff_required
@require_GET
def staff_attendance(request):
    batches = _staff_batches(request.user)
    active = batches.filter(is_active=True)
    day = parse_date(request.GET.get("date", "")) or timezone.localdate()
    batch_id = request.GET.get("batch", "")
    scope = active.filter(pk=batch_id) if batch_id.isdigit() else active
    memberships = GroupMembership.objects.filter(group__in=scope).select_related("student", "group")
    records = {a.student_id: a for a in Attendance.objects.filter(date=day)}
    on_leave = {
        l.student_id: l for l in LeaveRequest.objects.filter(
            status=LeaveRequest.APPROVED, start_date__lte=day, end_date__gte=day
        )
    }
    rows = []
    for m in sorted(memberships, key=lambda m: (m.group.name.lower(), m.student.username.lower())):
        record = records.get(m.student_id)
        if record:
            status = "Present"
        elif m.student_id in on_leave:
            status = "On leave"
        else:
            status = "Absent"
        rows.append({"student": m.student, "group": m.group, "record": record, "status": status})
    counts = {
        key: sum(r["status"] == label for r in rows)
        for key, label in (("Present", "Present"), ("On_leave", "On leave"), ("Absent", "Absent"))
    }
    return render(request, "tracker/staff/attendance.html", {
        "rows": rows, "counts": counts, "total": len(rows), "day": day,
        "batches": active, "selected_batch": batch_id,
        "is_today": day == timezone.localdate(),
    })


@staff_required
@require_GET
def staff_leaves(request):
    students = GroupMembership.objects.filter(group__in=_staff_batches(request.user)).values("student")
    leaves = LeaveRequest.objects.filter(student__in=students).select_related("student", "reviewed_by")
    status = request.GET.get("status", "pending")
    if status in {LeaveRequest.PENDING, LeaveRequest.APPROVED, LeaveRequest.REJECTED, LeaveRequest.CANCELLED}:
        leaves = leaves.filter(status=status)
    else:
        status = "all"
    page_obj = Paginator(leaves, 25).get_page(request.GET.get("page"))
    return render(request, "tracker/staff/leaves.html", {
        "page_obj": page_obj, "status": status,
        "pending_total": LeaveRequest.objects.filter(
            student__in=students, status=LeaveRequest.PENDING).count(),
        "today": timezone.localdate(),
    })


@staff_required
@require_POST
def staff_leave_review(request, pk, decision):
    if decision not in {"approve", "reject"}:
        raise PermissionDenied
    students = GroupMembership.objects.filter(group__in=_staff_batches(request.user)).values("student")
    leave = get_object_or_404(
        LeaveRequest, pk=pk, student__in=students, status=LeaveRequest.PENDING
    )
    leave.status = LeaveRequest.APPROVED if decision == "approve" else LeaveRequest.REJECTED
    leave.reviewed_by = request.user
    leave.reviewed_at = timezone.now()
    leave.review_note = request.POST.get("note", "").strip()[:300]
    leave.save()
    messages.success(request, f"Leave {leave.get_status_display().lower()} for {leave.student.username}.")
    return redirect("staff_leaves")
