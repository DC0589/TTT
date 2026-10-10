"""Minimal task runner. Synchronous by default; set BACKGROUND_TASKS=thread to offload."""
import logging
from concurrent.futures import Future, ThreadPoolExecutor

from django.conf import settings

logger = logging.getLogger(__name__)

_executor = None


def _pool():
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tracker-task")
    return _executor


def _safe(func, args, kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        logger.exception("Background task %s failed", getattr(func, "__name__", func))
        return None


def enqueue(func, *args, **kwargs):
    """Run func now, or on a worker thread when BACKGROUND_TASKS=thread. Returns a Future."""
    if getattr(settings, "BACKGROUND_TASKS", "sync") == "thread":
        return _pool().submit(_safe, func, args, kwargs)
    future = Future()
    future.set_result(_safe(func, args, kwargs))
    return future
