import json
import logging
import random
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


class GeminiAPIError(Exception):
    pass


def generate_json(parts):
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise GeminiAPIError("AI interview feedback is not configured.")

    models = list(dict.fromkeys((
        settings.GEMINI_MODEL,
        settings.GEMINI_FALLBACK_MODEL,
    )))
    payload = None
    last_failure = None
    for model_index, model in enumerate(models):
        attempts = 2 if model_index == 0 else 1
        for attempt in range(attempts):
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
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode())
                break
            except HTTPError as error:
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    raise GeminiAPIError(
                        f"The AI service returned HTTP {error.code}."
                    ) from None
                last_failure = f"HTTP {error.code} from {model}"
                logger.warning("Gemini model %s returned HTTP %s", model, error.code)
            except (URLError, TimeoutError) as error:
                reason = getattr(error, "reason", error)
                last_failure = f"connection error from {model}: {reason}"
                logger.warning("Gemini model %s connection failed: %s", model, reason)
            except json.JSONDecodeError as error:
                last_failure = f"invalid JSON from {model}: {error}"
                logger.warning("Gemini model %s returned invalid JSON: %s", model, error)
            if attempt + 1 < attempts:
                time.sleep(0.25 + random.uniform(0, 0.25))
        if payload is not None:
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