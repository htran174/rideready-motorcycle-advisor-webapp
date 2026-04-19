# services/images.py
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, Optional
from services.images_google import search_first_image
from services.images_google import GoogleImageSearchError

# -------------------------------
# Load images.json once (fast)
# -------------------------------

def _load_images_map() -> Dict[str, str]:
    """
    Load static/images.json as a dict of {brand_model_key: relative_path}.
    Example key: 'yamaha_yzfr3' -> 'stock_images/yamaha_r3.jpg'
    """
    here = Path(__file__).resolve()
    root = here.parent.parent              # project root (folder containing /services and /static)
    images_json = root / "static" / "images.json"
    with images_json.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # normalize keys to lowercase for safety
    return {str(k).strip().lower(): str(v).strip() for k, v in data.items()}

_IMAGES_MAP: Dict[str, str] = _load_images_map()


# -------------------------------
# Normalization helpers
# -------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+", re.IGNORECASE)

def _slug(s: str) -> str:
    """
    'YZF-R3' -> 'yzf_r3'
    'Ninja 400' -> 'ninja_400'
    """
    s = (s or "").lower().strip()
    s = _NON_ALNUM.sub("_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")

def _tight(s: str) -> str:
    """
    'YZF-R3' -> 'yzfr3'
    'Ninja 400' -> 'ninja400'
    """
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def _dedupe_brand(brand: str) -> str:
    """
    Collapse leading duplicate words: 'Yamaha Yamaha' -> 'Yamaha'.
    Harmless if already clean.
    """
    parts = (brand or "").strip().split()
    if len(parts) >= 2 and parts[0].lower() == parts[1].lower():
        parts = parts[1:]
    return " ".join(parts)

def _strip_brand_prefix(model: str, brand: str) -> str:
    """
    If model starts with the brand (e.g., 'Yamaha YZF-R3'), strip it → 'YZF-R3'.
    No-op if there's no prefix.
    """
    m = (model or "").strip()
    b = (brand or "").strip()
    if not m or not b:
        return m
    pattern = r"^" + re.escape(b) + r"[\s\-]+"
    return re.sub(pattern, "", m, flags=re.IGNORECASE).strip()

def _key_candidates(brand: str, model: str) -> Iterable[str]:
    """
    Generate candidate keys to look up in images.json.
    We try a few shapes so we’re robust to hyphens / underscores differences.
    """
    brand = _dedupe_brand(brand)
    model = _strip_brand_prefix(model, brand)

    sb = _slug(brand)     # e.g., 'yamaha'
    sm = _slug(model)     # e.g., 'yzf_r3'
    tm = _tight(model)    # e.g., 'yzfr3'

    # Most specific → least; common patterns first
    yield f"{sb}_{sm}"    # 'yamaha_yzf_r3'
    yield f"{sb}_{tm}"    # 'yamaha_yzfr3'
    # Rare fallbacks (some older maps might have used no underscore)
    yield f"{sb}{sm}"     # 'yamahayzf_r3'
    yield f"{sb}{tm}"     # 'yamahayzfr3'


# -------------------------------
# Optional Google fallback
# -------------------------------

def _google_first_image(query: str, logger_override=None) -> Optional[str]:
    active_logger = logger_override
    try:
        return search_first_image(query)
    except GoogleImageSearchError as exc:
        active_logger.warning("[images] Google fallback failed for query=%r error=%s", query, exc)
        return None
    except Exception:
        active_logger.exception("[images] Unexpected Google fallback failure for query=%r", query)
        return None

# -------------------------------
# Public resolver
# -------------------------------

def resolve_image_url(
    brand: str,
    model: str,
    image_query: str = "",
    logger=None,
) -> Optional[str]:
    """
    Attempt to resolve a local /static image for a bike.
    1) Normalize brand+model to keys like 'yamaha_yzfr3'
    2) Check static/images.json
    3) If miss, fallback to Google with image_query (or brand/model)
    Returns a URL (absolute or /static/...) or None.
    """
    # 1) local map lookup
    for key in _key_candidates(brand, model):
        path = _IMAGES_MAP.get(key)
        if path:
            url = f"/static/{path.lstrip('/')}"
            if logger:
                logger.info(f"[images] LOCAL  {brand} {model} -> {url} (key={key})")
            return url

    # 2) google fallback
    q = (image_query or "").strip() or f"{brand} {model}".strip()
    if logger:
        logger.info(f"[images] FALLBACK via Google: '{q}' (no local match)")
    return _google_first_image(q)



