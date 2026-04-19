import os
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GOOGLE_CSE_ENDPOINT = os.getenv(
    "GOOGLE_CSE_ENDPOINT",
    "https://www.googleapis.com/customsearch/v1",
)

GOOGLE_CSE_KEY = (
    os.getenv("GOOGLE_CSE_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or os.getenv("GOOGLE_SEARCH_API_KEY")
)
GOOGLE_CSE_CX = (
    os.getenv("GOOGLE_CSE_CX")
    or os.getenv("GOOGLE_CSE_ID")
    or os.getenv("GOOGLE_SEARCH_ENGINE_ID")
)

DEFAULT_TIMEOUT = float(os.getenv("GOOGLE_CSE_TIMEOUT", "10"))
DEFAULT_SAFE = os.getenv("GOOGLE_CSE_SAFE", "active")


class GoogleImageSearchError(RuntimeError):
    pass


def _validate_config() -> None:
    if not GOOGLE_CSE_KEY:
        raise GoogleImageSearchError(
            "Missing Google API key. Set GOOGLE_CSE_KEY (or GOOGLE_API_KEY / GOOGLE_SEARCH_API_KEY)."
        )
    if not GOOGLE_CSE_CX:
        raise GoogleImageSearchError(
            "Missing Programmable Search Engine ID. Set GOOGLE_CSE_CX (or GOOGLE_CSE_ID / GOOGLE_SEARCH_ENGINE_ID)."
        )


def search_first_image(query: str, *, session: Optional[requests.Session] = None) -> Optional[str]:
    query = (query or "").strip()
    if not query:
        return None

    _validate_config()

    http = session or requests
    params = {
        "key": GOOGLE_CSE_KEY,
        "cx": GOOGLE_CSE_CX,
        "q": query,
        "searchType": "image",
        "num": 1,
        "safe": DEFAULT_SAFE,
    }

    try:
        response = http.get(GOOGLE_CSE_ENDPOINT, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise GoogleImageSearchError(f"Google image search request failed: {exc}") from exc

    if response.status_code >= 400:
        details = ""
        try:
            payload = response.json()
            details = payload.get("error", {}).get("message", "")
        except ValueError:
            details = response.text[:300]

        raise GoogleImageSearchError(
            f"Google image search returned HTTP {response.status_code}. {details}".strip()
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise GoogleImageSearchError("Google image search returned non-JSON data.") from exc

    items = data.get("items") or []
    if not items:
        logger.info("[images_google] no results for query=%r", query)
        return None

    first = items[0] or {}
    link = first.get("link")
    if not isinstance(link, str) or not link.strip():
        logger.warning("[images_google] first result missing link for query=%r payload=%r", query, first)
        return None

    return link.strip()