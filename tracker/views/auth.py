import hmac
import logging
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.contrib.auth.views import LoginView
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from ..forms import (
    RegistrationOTPForm,
    StudentRegistrationForm,
)
from ..models import (
    StudentRegistrationRequest,
)
from ..throttle import client_ip, throttle_post
from ..throttle import count as throttle_count
from ..throttle import hit as throttle_hit

logger = logging.getLogger(__name__)

from ..permissions import dashboard_for
from ..services.registration import (
    OTP_LIFETIME,
    OTP_MAX_ATTEMPTS,
    OTP_MAX_RESENDS,
    OTP_RESEND_COOLDOWN,
    registration_code_hash,
    send_registration_otp,
)

LOGIN_FAILURE_LIMIT = 8


LOGIN_FAILURE_WINDOW = 15 * 60


class RoleLoginView(LoginView):
    redirect_authenticated_user = True

    def _failure_key(self):
        username = (self.request.POST.get("username") or "").strip().lower()
        return f"login-fail:{client_ip(self.request)}:{username}"

    def post(self, request, *args, **kwargs):
        if settings.THROTTLE_ENABLED and throttle_count(self._failure_key()) >= LOGIN_FAILURE_LIMIT:
            return HttpResponse(
                "Too many failed sign-in attempts. Please wait 15 minutes and try again.",
                status=429,
            )
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        if settings.THROTTLE_ENABLED:
            throttle_hit(self._failure_key(), LOGIN_FAILURE_WINDOW)
        return super().form_invalid(form)

    def get_default_redirect_url(self):
        from django.urls import reverse
        return reverse(dashboard_for(self.request.user))


def home(request):
    return redirect(dashboard_for(request.user) if request.user.is_authenticated else "login")


@throttle_post("register", 10, 3600)
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
                registration.verification_code_hash = registration_code_hash(
                    registration.pk, code
                )
                registration.save(update_fields=["verification_code_hash"])
        except IntegrityError:
            form.add_error(None, "A registration for this username or email is already pending.")
            return render(request, "registration/register.html", {"form": form})
        if not send_registration_otp(registration, code):
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
@throttle_post("otp-resend", 10, 3600)
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
        if not send_registration_otp(registration, code):
            messages.error(request, "We could not send another email. Check the address or try again later.")
            return redirect("verify_registration", pk=registration.pk)

        registration.verification_code_hash = registration_code_hash(registration.pk, code)
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
@throttle_post("otp-verify", 30, 3600)
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
        expected_hash = registration_code_hash(registration.pk, form.cleaned_data["code"])
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
