import json
import logging
import random
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


RETRY_ATTEMPTS = 3
RETRY_BUDGET_SECONDS = 30


class GeminiAPIError(Exception):
    pass


def _retry_after_seconds(error):
    try:
        return min(float(error.headers.get("Retry-After", "")), 5.0)
    except (TypeError, ValueError, AttributeError):
        return None


def _backoff_seconds(attempt, retry_after=None):
    """Exponential backoff with jitter so many students do not retry in lockstep."""
    base = retry_after if retry_after else min(0.5 * 2 ** attempt, 3.0)
    return base + random.uniform(0, 0.5)


def generate_json(parts, deadline=None):
    """Call Gemini. `deadline` caps total seconds so serverless requests cannot overrun."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise GeminiAPIError("AI interview feedback is not configured.")

    models = list(dict.fromkeys((
        settings.GEMINI_PRIMARY_MODEL,
        settings.GEMINI_FALLBACK_MODEL,
    )))
    payload = None
    last_failure = None
    started = time.monotonic()
    limit = deadline if deadline else RETRY_BUDGET_SECONDS + 20
    out_of_time = False
    for model_index, model in enumerate(models):
        attempts = RETRY_ATTEMPTS if model_index == 0 else 1
        for attempt in range(attempts):
            remaining = limit - (time.monotonic() - started)
            if (attempt or model_index) and remaining < 3:
                out_of_time = True
                break
            if deadline is None and payload is None and attempt and time.monotonic() - started > RETRY_BUDGET_SECONDS:
                break
            retry_after = None
            request = Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                data=json.dumps({
                    "contents": [{"parts": parts}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "thinkingConfig": {"thinkingLevel": "LOW"},
                    },
                }).encode(),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=max(3, min(20, remaining))) as response:
                    payload = json.loads(response.read().decode())
                break
            except HTTPError as error:
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    raise GeminiAPIError(
                        f"The AI service returned HTTP {error.code}."
                    ) from None
                last_failure = f"HTTP {error.code} from {model}"
                retry_after = _retry_after_seconds(error)
                logger.warning("Gemini model %s returned HTTP %s", model, error.code)
            except (URLError, TimeoutError) as error:
                reason = getattr(error, "reason", error)
                last_failure = f"connection error from {model}: {reason}"
                logger.warning("Gemini model %s connection failed: %s", model, reason)
            except json.JSONDecodeError as error:
                last_failure = f"invalid JSON from {model}: {error}"
                logger.warning("Gemini model %s returned invalid JSON: %s", model, error)
            if attempt + 1 < attempts:
                time.sleep(min(_backoff_seconds(attempt, retry_after), max(0, remaining - 3)))
        if payload is not None or out_of_time:
            break

    if payload is None:
        logger.error("Gemini primary and fallback models failed: %s", last_failure)
        raise GeminiAPIError(
            "The AI service is unavailable right now. Please try again shortly."
        ) from None

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise GeminiAPIError("The AI service returned an unreadable response.") from None

    if not isinstance(result, dict):
        raise GeminiAPIError("The AI service returned an unreadable response.")
    return result

HEALTH_CACHE_KEY = "ai_interview_health"
HEALTH_CACHE_SECONDS = 60


def _probe_model(model, api_key):
    request = Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps({
            "contents": [{"parts": [{"text": "Reply with the word ok."}]}],
            "generationConfig": {"maxOutputTokens": 8},
        }).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=8) as response:
            json.loads(response.read().decode())
    except HTTPError as error:
        return {"model": model, "ok": False, "detail": f"HTTP {error.code}"}
    except (URLError, TimeoutError) as error:
        return {"model": model, "ok": False, "detail": str(getattr(error, "reason", error))}
    except json.JSONDecodeError:
        return {"model": model, "ok": False, "detail": "unreadable response"}
    return {"model": model, "ok": True, "latency_ms": int((time.monotonic() - started) * 1000)}


def check_health(force=False):
    from django.core.cache import cache

    if not force:
        cached = cache.get(HEALTH_CACHE_KEY)
        if cached:
            return cached
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        result = {"status": "down", "message": "AI is not configured.", "models": []}
    else:
        models = list(dict.fromkeys((
            settings.GEMINI_PRIMARY_MODEL, settings.GEMINI_FALLBACK_MODEL,
        )))
        checks = []
        for model in models:
            check = _probe_model(model, api_key)
            checks.append(check)
            if check["ok"]:
                break
        if checks[0]["ok"]:
            result = {"status": "ok", "message": "AI is working, Start giving your mock.", "models": checks}
        elif checks[-1]["ok"]:
            result = {"status": "degraded", "message": "AI is running on the backup model.", "models": checks}
        else:
            result = {"status": "down", "message": "AI is not responding right now. Please try again shortly.", "models": checks}
    for check in result["models"]:
        if not check["ok"]:
            logger.warning("AI health check: %s failed (%s)", check["model"], check["detail"])
    cache.set(HEALTH_CACHE_KEY, result, HEALTH_CACHE_SECONDS)
    return result
