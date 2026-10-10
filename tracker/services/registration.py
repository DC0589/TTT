import hashlib
import hmac
from datetime import timedelta

from django.conf import settings

from .notifications import send_notification

OTP_LIFETIME = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = timedelta(seconds=60)
OTP_MAX_RESENDS = 3


def registration_code_hash(registration_id, code):
    message = f"{registration_id}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def send_registration_otp(registration, code):
    return send_notification(
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
