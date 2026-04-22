import os
import time
from flask import Flask, render_template, request, jsonify, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Services
from services.chat_nlu import make_plan
from services.images import resolve_image_url
from services.recommend_rules import load_bikes, apply_filters, pick_reasons

APP_TITLE = "RideReady"
APP_BOOT_ID = str(int(time.time()))

app = Flask(__name__)

# Simple dev limiter (in-memory). Fine for local work.
limiter = Limiter(get_remote_address, app=app, default_limits=["200/day"])

# -------------------- helpers --------------------

def _history_entry_from_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None

    brand = item.get("brand") or item.get("manufacturer")
    model = item.get("model") or item.get("name")

    if not brand and not model:
        return None

    return {
        "brand": brand,
        "model": model,
    }

def _merge_history(old_history: list[dict] | None, items: list[dict] | None) -> list[dict]:
    merged = []
    seen = set()

    for entry in old_history or []:
        if not isinstance(entry, dict):
            continue
        key = _bike_identity(entry.get("brand"), entry.get("model"))
        if key == ("", "") or key in seen:
            continue
        seen.add(key)
        merged.append({
            "brand": entry.get("brand"),
            "model": entry.get("model"),
        })

    for item in items or []:
        entry = _history_entry_from_item(item)
        if not entry:
            continue
        key = _bike_identity(entry.get("brand"), entry.get("model"))
        if key == ("", "") or key in seen:
            continue
        seen.add(key)
        merged.append(entry)

    return merged

def _bike_identity(brand: str | None, model: str | None) -> tuple[str, str]:
    return (
        (brand or "").strip().lower(),
        (model or "").strip().lower(),
    )

def _history_set(recommended_history: list[dict] | None) -> set[tuple[str, str]]:
    out = set()
    for item in recommended_history or []:
        if not isinstance(item, dict):
            continue
        brand = item.get("brand") or item.get("manufacturer")
        model = item.get("model") or item.get("name")
        key = _bike_identity(brand, model)
        if key != ("", ""):
            out.add(key)
    return out

def _item_identity(item: dict) -> tuple[str, str]:
    brand = item.get("brand") or item.get("manufacturer")
    model = item.get("model") or item.get("name")
    return _bike_identity(brand, model)

def local_image_url(local_image: str | None) -> str:
    if not local_image:
        return url_for("static", filename="stock_images/motorcycle_ride.jpg")
    if not local_image.startswith("stock_images/"):
        local_image = f"stock_images/{local_image}"
    return url_for("static", filename=local_image)

def _clean_profile(data: dict) -> dict:
    return {
        "experience": data.get("experience") or "no_experience",
        "height_cm": _to_int(data.get("height_cm"), 170),
        "budget_usd": _to_float(data.get("budget_usd"), 999999.0),
        "bike_types": data.get("bike_types") or [],
        "k": max(1, min(6, _to_int(data.get("k"), 2))),
    }

def _run_recommend(
        profile: dict,
        pin_ids: list[str] | None = None,
        external_items: list[dict] | None = None,
        recommended_history: list[dict] | None = None,):

    profile = _clean_profile(profile)
    history_seen = _history_set(recommended_history)

    #Load catalog
    bikes = load_bikes()
    if isinstance(bikes, tuple):
        bikes = bikes[0]
    # Keep only dict bikes
    bikes = [b for b in (bikes or []) if isinstance(b, dict)]


    filtered = apply_filters(bikes, profile)
    if isinstance(filtered, tuple):
        filtered = filtered[0]
    filtered = [b for b in (filtered or []) if isinstance(b, dict)]

    picked = []
    try:
        pr = pick_reasons(filtered, profile)
        if isinstance(pr, tuple) and len(pr) >= 1:
            picked = pr[0]
        else:
            picked = pr
    except Exception as e:
        picked = filtered

    picked = [b for b in (picked or []) if isinstance(b, dict)]
    by_id = {b.get("id"): b for b in bikes if isinstance(b, dict) and b.get("id")}

    # Utility to dedupe by (id) or (mfr,name)
    seen = set()
    out = []

    def _key(it):
        if not isinstance(it, dict):
            return None
        if it.get("id"):
            return ("id", it["id"])
        return ("mk", (it.get("manufacturer", "").lower(), it.get("name", "").lower()))

    def _add(it):
        if not isinstance(it, dict):
            return

        identity = _item_identity(it)
        if identity != ("", "") and identity in history_seen:
            return

        k = _key(it)
        if not k or k in seen:
            return

        seen.add(k)
        out.append(it)

    # 4) Pins first (catalog ids)
    if pin_ids:
        for pid in pin_ids:
            b = by_id.get(pid)
            if b:
                _add(b)

    # 5) Externals
    if external_items:
        for ext in external_items:
            try:
                norm = _normalize_external(ext)
                _add(norm)
            except Exception as e:
                print(f"[recommend] bad external item skipped: {e}")

    # 6) Rule-based picks
    for it in picked:
        _add(it)

    # 7) Enforce k
    k = profile["k"]  # already clamped in _clean_profile
    return out[:k]



def _guess_manufacturer(label: str | None) -> str | None:
    if not label:
        return None
    # "BMW G 310 R" -> "BMW"
    return label.split()[0]

def _normalize_external(ext: dict) -> dict:
    """
    Convert an external item (not in our catalog) into the same
    shape frontend cards expect.
    """
    specs = ext.get("specs") or {}
    label = ext.get("label") or ext.get("name") or "Motorcycle"
    return {
        # id is intentionally None for non-catalog items
        "id": None,
        "name": label,
        "manufacturer": ext.get("manufacturer") or _guess_manufacturer(label),
        "category": specs.get("category") or ext.get("category"),
        "engine_cc": specs.get("engine_cc"),
        "seat_height_mm": specs.get("seat_height_mm"),
        "wet_weight_kg": specs.get("wet_weight_kg"),
        "abs": specs.get("abs"),
        "top_speed_mph": specs.get("max_speed_mph") or ext.get("top_speed_mph"),
        "zero_to_sixty_s": specs.get("zero_to_sixty_s") or ext.get("zero_to_sixty_s"),
        "official_url": ext.get("official_url"),
        "mfr_domain": ext.get("mfr_domain"),
        # frontend /images endpoint will use this first; local stock image remains fallback
        "image_query": ext.get("image_query") or label,
    }

def _to_int(val, default=None):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        try:
            return int(float(val))
        except Exception:
            return default

def _to_float(val, default=None):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# -------------------- pages ----------------------

@app.route("/")
def home():
    return render_template("home.html",
                           app_title=APP_TITLE, page_title="Home", boot_id=APP_BOOT_ID)

@app.route("/advisor")
def advisor():
    return render_template("advisor.html",
                           app_title=APP_TITLE, page_title="Advisor", boot_id=APP_BOOT_ID)

@app.route("/recommendations")
def recommendations():
    return render_template("recommendations.html",
                           app_title=APP_TITLE, page_title="Recommendations", boot_id=APP_BOOT_ID)

@app.route("/disclaimer")
def disclaimer():
    return render_template("disclaimer.html",
                           app_title=APP_TITLE, page_title="Disclaimer", boot_id=APP_BOOT_ID)

# -------------------- APIs ----------------------

@app.route("/api/recommend", methods=["POST"])
@limiter.limit("12/minute;120/day")
def api_recommend():
    data = request.get_json(silent=True) or {}
    profile = _clean_profile(data)                     # <-- sanitize
    pin_ids = data.get("pin_ids") or []
    external_items = data.get("external_items") or []
    items = _run_recommend(
        profile,
        pin_ids=pin_ids,
        external_items=external_items,
    )
    return jsonify({"items": items})

@app.route("/api/images", methods=["POST"])
@limiter.limit("60/minute;600/day")
def api_images():
    data = request.get_json(silent=True) or {}

    brand = data.get("manufacturer") or data.get("brand") or ""
    model = data.get("model") or data.get("name") or ""
    image_query = data.get("image_query") or f"{brand} {model}".strip()

    url = resolve_image_url(brand, model, image_query)

    app.logger.info(f"[images] {brand} {model} -> {url or 'NONE'}")
    return jsonify({ "url": url })

@app.route("/api/chat", methods=["POST"])
@limiter.limit("12/minute;120/day")
def api_chat():
    """
    Returns a plan + optional embedded recommendations so the Chat tab
    can render inline “card bubbles,” while the Recs tab stays in sync.
    """
    data = request.get_json(silent=True) or {}
    
    msg = data.get("message", "") or ""
    profile = data.get("profile") or {}
    recommended_history = data.get("recommended_history") or []

    plan = make_plan(
        msg,
        profile,
        recommended_history=recommended_history,
        logger=app.logger,
    )

    pin_ids = []
    external_items = plan.get("external_items") or []

    for act in plan.get("actions", []):
        if act.get("type") == "UPDATE_PROFILE":
            profile.update(act.get("patch", {}) or {})
        elif act.get("type") == "RECOMMEND":
            pin_ids.extend(act.get("pin_ids") or [])

    items = _run_recommend(
        profile,
        pin_ids=pin_ids,
        external_items=external_items,
        recommended_history=recommended_history,
    )

    updated_history = _merge_history(recommended_history, items)

    return jsonify({
        "topic": plan.get("topic", "motorcycle_recommendation"),
        "message": plan.get("message", ""),
        "actions": plan.get("actions", []),
        "items": items,
        "profile": profile,
        "recommended_history": updated_history,
    })

@app.route("/healthz")
def healthz():
    limits = {
        "recommend": "30/minute;300/day",
        "images": "60/minute;600/day",
        "chat": "12/minute;120/day",
        "default_daily": "200/day",
    }
    ok = os.path.exists(os.path.join("data", "bikes.json"))
    return jsonify({
        "ok": ok,
        "bikes_json": ok,
        "limits": limits,
        "openai_enabled": os.getenv("RR_OPENAI_ENABLED", "false").lower() == "true"
    })

if __name__ == "__main__":
    app.run(debug=True)
