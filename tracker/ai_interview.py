import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


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
            "generationConfig": {"responseMimeType": "application/json"},
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
    except HTTPError as error:
        raise GeminiAPIError(f"The AI service returned HTTP {error.code}.") from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise GeminiAPIError("The AI service could not be reached. Please try again.") from None

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise GeminiAPIError("The AI service returned an unreadable response.") from None

    if not isinstance(result, dict):
        raise GeminiAPIError("The AI service returned an unreadable response.")
    return result