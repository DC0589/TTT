import logging
from smtplib import SMTPException

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_notification(subject, body, recipients, context, template_name, email_context):
    """Send an HTML email. Returns False (and logs) when delivery fails."""
    try:
        message = EmailMultiAlternatives(subject, body, settings.DEFAULT_FROM_EMAIL, recipients)
        message.attach_alternative(render_to_string(template_name, email_context), "text/html")
        message.send(fail_silently=False)
    except (OSError, SMTPException, ValueError):
        logger.exception("Email delivery failed: %s", context)
        return False
    return True
