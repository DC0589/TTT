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

    model = settings.GEMINI_MODEL
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

    for attempt in range(2):
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode())
            break
        except HTTPError as error:
            if error.code == 503 and attempt == 0:
                time.sleep(0.25 + random.uniform(0, 0.25))
                continue
            if error.code == 503:
                raise GeminiAPIError(
                    "The AI service is temporarily unavailable. Please try again shortly."
                ) from None
            raise GeminiAPIError(f"The AI service returned HTTP {error.code}.") from None
        except (URLError, TimeoutError) as error:
            reason = getattr(error, "reason", error)
            logger.warning("Gemini connection failed on attempt %s: %s", attempt + 1, reason)
            if attempt == 0:
                time.sleep(0.25 + random.uniform(0, 0.25))
                continue
            raise GeminiAPIError(
                "The AI service could not be reached after retrying. Check server network access and try again."
            ) from None
        except json.JSONDecodeError as error:
            logger.warning("Gemini returned invalid JSON: %s", error)
            raise GeminiAPIError("The AI service returned an unreadable response.") from None

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise GeminiAPIError("The AI service returned an unreadable response.") from None

    if not isinstance(result, dict):
        raise GeminiAPIError("The AI service returned an unreadable response.")
    return result