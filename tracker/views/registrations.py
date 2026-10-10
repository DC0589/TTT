import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import (
    StudentRegistrationRequest,
    User,
)

logger = logging.getLogger(__name__)

from ..permissions import staff_required
from ..services.notifications import send_notification


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
    if not send_notification(
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
    if not send_notification(
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
