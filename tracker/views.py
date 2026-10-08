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
from datetime import timedelta
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
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
    AddMemberForm, FinalStatusForm, GroupForm, HRUserForm, InterviewForm, LearningCourseForm,
    MockQuestionForm, RegistrationOTPForm, RoundForm, RoundStatusForm, StudentForm,
    StudentRegistrationForm,
)
from .mock_bank import DIFFICULTY_GUIDE, normalise_difficulty, pick_seed_questions
from .mock_topics import MOCK_TOPICS, pick_focus_areas, topic_language
from .ai_interview import GeminiAPIError, generate_json
from .models import (
    Group, GroupMembership, Interview, InterviewRound, InterviewStatus,
    LearningCourse, MockInterviewScore, MockInterviewSession, MockQuestion,
    StudentRegistrationRequest, User,
)

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
        "Your Interview Tracker verification code",
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
            "preheader": "Your one-time code to verify your student registration.",
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
    admin_emails = list(
        User.objects.filter(is_admin=True, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )
    if not admin_emails and settings.ADMIN_EMAIL:
        admin_emails = [settings.ADMIN_EMAIL]
    if admin_emails:
        approval_url = request.build_absolute_uri(reverse("admin_registrations"))
        if not _send_notification(
            "Student registration awaiting approval",
            "Hello Administrator,\n\n"
            f"{registration.username} ({registration.email}) verified their email and is awaiting approval.\n"
            f"Review the request: {approval_url}",
            admin_emails,
            f"admin notification for registration {registration.pk}",
            "emails/admin_registration.html",
            {
                "greeting_name": "Administrator",
                "headline": "A student signup is ready for review",
                "intro": "A prospective student has verified their email address and is waiting for your decision.",
                "student_name": registration.username,
                "student_email": registration.email,
                "action_label": "Review signup request",
                "action_url": approval_url,
                "preheader": f"{registration.username} verified their email and is awaiting approval.",
            },
        ):
            messages.warning(request, "Your email is verified, but the administrator notification could not be sent. Your request remains visible in the admin dashboard.")
    else:
        logger.error(
            "Verified student registration %s has no active admin email recipient",
            registration.pk,
        )
        messages.warning(request, "Your email is verified, but the administrator could not be notified. The request is available in the admin dashboard.")
    messages.success(request, "Email verified. Your account will be created after an administrator approves your request.")
    return redirect(f"{reverse('registration_submitted')}?verified=1")


# ---------- Admin ----------
@admin_required
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
    return render(request, "tracker/admin/dashboard.html", {
        "stats": stats, "interviews": interviews[:20],
        "students": students.order_by("username")[:8],
        "outcome_chart": outcome_chart,
        "selection_rate": round(outcome_counts["selected"] * 100 / decided) if decided else 0,
        "pending_registrations": StudentRegistrationRequest.objects.filter(
            status=StudentRegistrationRequest.AWAITING_APPROVAL
        ).count(),
        "upcoming_interviews": interviews.filter(
            date_of_interview__gte=timezone.localdate()
        ).order_by("date_of_interview", "company_name")[:5],
    })


@admin_required
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


@staff_required
def admin_interviews(request):
    owner = request.user.created_by if request.user.is_hr else request.user
    view_mode = request.GET.get("view", "cards")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "cards"
    interviews = (
        Interview.objects.filter(group__admin=owner)
        .select_related("student", "group", "status")
        .prefetch_related("rounds")
    )
    return render(request, "tracker/admin/interviews.html", {
        "interviews": interviews,
        "view_mode": view_mode,
    })


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
    return render(request, "tracker/admin/interview_detail.html", {
        "iv": interview,
    })


@admin_required
def registrations_pending_count(request):
    count = StudentRegistrationRequest.objects.filter(
        status=StudentRegistrationRequest.AWAITING_APPROVAL
    ).count()
    return JsonResponse({"count": count})


@admin_required
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


@admin_required
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


@admin_required
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
    view_mode = request.GET.get("view", "cards")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "cards"
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
    return render(request, "tracker/admin/student_detail.html", {
        "student": student,
        "memberships": student.memberships.filter(
            group__admin=request.user).select_related("group"),
        "interviews": interviews,
    })


@staff_required
@require_GET
def admin_groups(request):
    view_mode = request.GET.get("view", "cards")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "cards"
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


@student_required
def student_dashboard(request):
    interviews = request.user.interviews.select_related("group", "status").prefetch_related("rounds")
    return render(request, "tracker/student/dashboard.html", {
        "groups": request.user.student_groups.all(), "interviews": interviews[:5],
        "active_tab": "overview",
        "total": interviews.count(),
        "selected": interviews.filter(status__final_status="selected").count(),
        "in_progress": interviews.filter(status__isnull=True).count(),
        "upcoming_interviews": interviews.filter(
            date_of_interview__gte=timezone.localdate()
        ).order_by("date_of_interview", "company_name")[:5],
    })


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
    return {"topics": rows, "levels": levels, "trend": trend, "suggestion": suggestion}


@student_required
@require_GET
def student_mock_interview(request):
    return render(request, "tracker/student/mock_interview.html", {
        "progress": _mock_progress(request.user),
        "ai_url": reverse("student_mock_interview_ai"),
        "topics": list(MOCK_TOPICS) + sorted(
            set(MockQuestion.objects.filter(is_active=True).values_list("topic", flat=True)) - set(MOCK_TOPICS)
        ),
        "active_tab": "mock_interview",
        "recent_sessions": request.user.mock_interview_sessions.prefetch_related(
            "scores"
        )[:8],
    })


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
    if data.get("lite") is True:
        camera_feedback = screen_feedback = ""
    else:
        camera_feedback = str(result.get("camera_feedback", "No camera feedback available."))[:500]
        screen_feedback = str(result.get("screen_feedback", "No screen feedback available."))[:500]
    score_record.status = MockInterviewScore.COMPLETE
    score_record.score = score
    score_record.answer_feedback = answer_feedback
    score_record.camera_feedback = camera_feedback
    score_record.screen_feedback = screen_feedback
    score_record.save(update_fields=[
        "status", "score", "answer_feedback", "camera_feedback", "screen_feedback",
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
        messages.success(request, "Interview added. Now add its rounds.")
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
    form = RoundForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    with transaction.atomic():
        nxt = (iv.rounds.aggregate(m=Max("round_number"))["m"] or 0) + 1
        rnd = InterviewRound.objects.create(
            interview=iv, round_number=nxt, description=form.cleaned_data["description"])
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
        "badge": rnd.badge,
        "final_status": rnd.interview.final_status,
        "final_label": rnd.interview.final_label,
    })


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
