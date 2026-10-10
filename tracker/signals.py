from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

from .context_processors import BANNER_CACHE_KEY
from .models import InterviewStatus, PlacedStudent, Selection


def invalidate_banner_cache(**kwargs):
    cache.delete(BANNER_CACHE_KEY)


for _model in (PlacedStudent, Selection, InterviewStatus):
    post_save.connect(invalidate_banner_cache, sender=_model, weak=False)
    post_delete.connect(invalidate_banner_cache, sender=_model, weak=False)
