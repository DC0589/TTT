from smtplib import SMTPException

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a test email using the configured SMTP settings."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Recipient email address")

    def handle(self, *args, **options):
        if not settings.EMAIL_BACKEND.endswith(".smtp.EmailBackend"):
            raise CommandError(
                "SMTP is not enabled. Set EMAIL_HOST in .env to enable SMTP delivery."
            )
        missing = [
            name for name, value in (
                ("EMAIL_HOST", settings.EMAIL_HOST),
                ("EMAIL_HOST_USER", settings.EMAIL_HOST_USER),
                ("EMAIL_HOST_PASSWORD", settings.EMAIL_HOST_PASSWORD),
                ("DEFAULT_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL),
            )
            if not value
        ]
        if missing:
            raise CommandError(
                "Email is not configured. Set these .env values: "
                + ", ".join(missing)
            )
        try:
            sent = send_mail(
                "Interview Tracker email test",
                "SMTP is configured and the Interview Tracker can send email.",
                settings.DEFAULT_FROM_EMAIL,
                [options["to"]],
                fail_silently=False,
            )
        except (OSError, SMTPException, ValueError) as exc:
            raise CommandError(
                f"SMTP delivery failed ({type(exc).__name__}). Check EMAIL_HOST, EMAIL_PORT, "
                "EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, and "
                "the provider's account security settings."
            ) from exc
        if sent != 1:
            raise CommandError("The email backend did not confirm delivery.")
        self.stdout.write(self.style.SUCCESS("Test email accepted by the SMTP server."))
