from functools import wraps
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import timedelta
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
from django.db.models import Count, Max, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
    AddMemberForm, FinalStatusForm, GroupForm, InterviewForm, LearningCourseForm,
    RegistrationOTPForm, RoundForm, RoundStatusForm, StudentForm,
    StudentRegistrationForm,
)
from .ai_interview import GeminiAPIError, generate_json
from .models import (
    Group, GroupMembership, Interview, InterviewRound, InterviewStatus,
    LearningCourse, StudentRegistrationRequest, User,
)

logger = logging.getLogger(__name__)
OTP_LIFETIME = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5


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
student_required = role_required("is_student")


def dashboard_for(user):
    return "admin_dashboard" if user.is_admin else "student_dashboard"


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
        try:
            with transaction.atomic():
                registration = StudentRegistrationRequest.objects.create(
                    username=form.cleaned_data["username"],
                    email=form.cleaned_data["email"],
                    password_hash=make_password(form.cleaned_data["password1"]),
                    verification_code_hash="",
                    verification_expires_at=timezone.now() + OTP_LIFETIME,
                )
                registration.verification_code_hash = _registration_code_hash(
                    registration.pk, code
                )
                registration.save(update_fields=["verification_code_hash"])
        except IntegrityError:
            form.add_error(None, "A registration for this username or email is already pending.")
            return render(request, "registration/register.html", {"form": form})
        if not _send_notification(
            "Your Interview Tracker verification code",
            f"Hello {registration.username},\n\n"
            f"Your email verification code is: {code}\n\n"
            "Enter this code on the registration page within 10 minutes. "
            "You have up to 5 attempts. After verification, an administrator "
            "must approve your account request.\n\n"
            "If you did not request this account, you can ignore this email.",
            [registration.email],
            f"student registration {registration.pk}",
            "emails/registration_otp.html",
            {
                "greeting_name": registration.username,
                "headline": "Verify your email address",
                "intro": "Thanks for creating an account request with Tweak Talent Technologies. Enter this code to confirm that this email address belongs to you.",
                "code": code,
                "expiry_minutes": int(OTP_LIFETIME.total_seconds() // 60),
                "attempt_limit": OTP_MAX_ATTEMPTS,
                "preheader": "Your one-time code to verify your student registration.",
            },
        ):
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
                "email_backend_console": settings.EMAIL_BACKEND.endswith(
                    ".console.EmailBackend"
                ),
            })

        form = RegistrationOTPForm(request.POST)
        if not form.is_valid():
            return render(request, "registration/verify_email.html", {
                "registration": registration,
                "form": form,
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
        Q(memberships__group__admin=request.user) | Q(created_by=request.user)
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
def admin_student_add(request):
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = form.save(commit=False)
        student.created_by = request.user
        student.save()
        messages.success(request, "Student added.")
        return redirect("admin_student_detail", pk=student.pk)
    return render(request, "tracker/admin/student_form.html", {
        "form": form,
        "page_title": "Add a student",
    })


@admin_required
def admin_interviews(request):
    view_mode = request.GET.get("view", "cards")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "cards"
    interviews = (
        Interview.objects.filter(group__admin=request.user)
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


@admin_required
def admin_interview_detail(request, pk):
    interview = get_object_or_404(
        Interview.objects.select_related("student", "group", "status").prefetch_related("rounds"),
        pk=pk,
        group__admin=request.user,
    )
    return render(request, "tracker/admin/interview_detail.html", {
        "iv": interview,
    })


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
            Q(memberships__group__admin=request.user) | Q(created_by=request.user)
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
        Q(memberships__group__admin=request.user) | Q(created_by=request.user)
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
def admin_student_detail(request, pk):
    student = get_object_or_404(
        User.objects.filter(is_student=True).filter(
            Q(memberships__group__admin=request.user) | Q(created_by=request.user)
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


@admin_required
@require_GET
def admin_groups(request):
    view_mode = request.GET.get("view", "cards")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "cards"
    groups = request.user.groups_created.annotate(
        member_count=Count("memberships", distinct=True),
        interview_count=Count("interviews", distinct=True),
    ).order_by("name")
    return render(request, "tracker/admin/groups.html", {
        "groups": groups,
        "view_mode": view_mode,
    })


@admin_required
def admin_group_add(request):
    form = GroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.admin = request.user
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


@admin_required
@require_GET
def admin_group_detail(request, pk):
    group = get_object_or_404(Group, pk=pk, admin=request.user)
    return render(request, "tracker/admin/group_detail.html", {
        "group": group,
        "memberships": group.memberships.select_related("student"),
        "interviews": Interview.objects.filter(
            student__memberships__group=group,
            group__admin=request.user,
        ).select_related("student", "group", "status").prefetch_related("rounds")})


@admin_required
def admin_group_member_add(request, pk):
    group = get_object_or_404(Group, pk=pk, admin=request.user)
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


@student_required
@require_GET
def student_mock_interview(request):
    return render(request, "tracker/student/mock_interview.html", {
        "ai_url": reverse("student_mock_interview_ai"),
        "active_tab": "mock_interview",
    })


@student_required
@require_POST
def student_mock_interview_ai(request):
    if len(request.body) > 1_250_000:
        return JsonResponse({"error": "The request is too large. Try again."}, status=413)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not isinstance(data, dict) or data.get("consent") is not True:
        return JsonResponse({"error": "Consent is required before AI analysis."}, status=400)

    action = data.get("action")
    role = data.get("role", "")
    question_number = data.get("question_number")
    if action not in {"question", "feedback"} or not isinstance(role, str):
        return JsonResponse({"error": "Invalid interview request."}, status=400)
    role = role.strip()[:120]
    if not role or not isinstance(question_number, int) or not 1 <= question_number <= 5:
        return JsonResponse({"error": "Enter a role and a valid question number."}, status=400)

    history = data.get("history", [])
    if not isinstance(history, list):
        return JsonResponse({"error": "Invalid interview history."}, status=400)
    history_text = "\n".join(
        f"Q: {item.get('question', '')[:300]}\nA: {item.get('answer', '')[:1200]}"
        for item in history[-4:]
        if isinstance(item, dict)
        and isinstance(item.get("question"), str)
        and isinstance(item.get("answer"), str)
    )

    if action == "question":
        parts = [{"text": (
            "You are a concise mock interviewer. Ask exactly one realistic question for "
            f"the role '{role}'. This is question {question_number} of 5. "
            "Do not repeat earlier questions. Return JSON with one string field: question.\n"
            f"Earlier questions and answers:\n{history_text or 'None'}"
        )}]
    else:
        question = data.get("question", "")
        answer = data.get("answer", "")
        frames = data.get("frames", {})
        if not isinstance(question, str) or not question.strip() or not isinstance(answer, str):
            return JsonResponse({"error": "Add your answer before requesting feedback."}, status=400)
        if not answer.strip() or len(answer) > 4000:
            return JsonResponse({"error": "Answers must contain 1 to 4,000 characters."}, status=400)
        if not isinstance(frames, dict):
            return JsonResponse({"error": "Camera and screen snapshots are required."}, status=400)

        parts = [{"text": (
            "Evaluate this mock interview answer. Use the transcript to assess relevance and clarity. "
            "Use the camera image only for framing, lighting, and visibility. Use the screen image only "
            "for readability and relevance to the role. Do not infer identity, age, gender, race, health, "
            "or personality from appearance. Give supportive, specific feedback. "
            "Return JSON with integer score from 1 to 5, strings answer_feedback, camera_feedback, "
            "screen_feedback, and next_question. Set next_question to null on question 5; otherwise "
            "ask exactly one new question for the same role.\n"
            f"Role: {role}\nQuestion {question_number}: {question[:500]}\n"
            f"Student answer transcript: {answer.strip()}\nEarlier Q&A:\n{history_text or 'None'}"
        )}]
        for frame_name, label in (("camera", "Camera snapshot"), ("screen", "Shared-screen snapshot")):
            frame = frames.get(frame_name, "")
            match = re.fullmatch(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", frame) if isinstance(frame, str) else None
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

    try:
        result = generate_json(parts)
    except GeminiAPIError as error:
        status = 503 if "not configured" in str(error) else 502
        return JsonResponse({"error": str(error)}, status=status)

    if action == "question":
        question = result.get("question")
        if not isinstance(question, str) or not question.strip():
            return JsonResponse({"error": "The AI did not return a question. Please try again."}, status=502)
        return JsonResponse({"question": question.strip()[:500]})

    score = result.get("score", 3)
    if not isinstance(score, int):
        score = 3
    return JsonResponse({
        "score": max(1, min(score, 5)),
        "answer_feedback": str(result.get("answer_feedback", "Review your answer and try again."))[:800],
        "camera_feedback": str(result.get("camera_feedback", "No camera feedback available."))[:500],
        "screen_feedback": str(result.get("screen_feedback", "No screen feedback available."))[:500],
        "next_question": result.get("next_question") if isinstance(result.get("next_question"), str) else None,
    })


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
