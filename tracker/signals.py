from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

from .context_processors import BANNER_CACHE_KEY
from .models import InterviewStatus, PlacedStudent, Selection
from .services import sessions as session_service


def invalidate_banner_cache(**kwargs):
    cache.delete(BANNER_CACHE_KEY)


for _model in (PlacedStudent, Selection, InterviewStatus):
    post_save.connect(invalidate_banner_cache, sender=_model, weak=False)
    post_delete.connect(invalidate_banner_cache, sender=_model, weak=False)


def record_login(sender, request, user, **kwargs):
    session_service.start_session(request, user)


def record_logout(sender, request, user, **kwargs):
    if user is not None:
        session_service.end_session(request, user)


user_logged_in.connect(record_login, weak=False)
user_logged_out.connect(record_logout, weak=False)
