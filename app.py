"""WikiMasters Collection Manager - backend Flask."""
import csv
import io
import json
import logging
import os
import threading
import time
from collections import Counter, defaultdict

from flask import Flask, Response, jsonify, render_template, request

import config
from price_cache import PriceCache
from wikimasters_api import (
    RARITY_LABELS,
    RARITY_MULTIPLIERS,
    APIError,
    AuthenticationError,
    CardAnalyzer,
    DemoClient,
    WikiMastersClient,
    enrich_cards,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wikimasters")

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY

state = {
    "client": None,
    "cards": None,
    "loaded_at": None,
    "error": None,
    "loading": False,
    "progress": None,
    "load_gen": 0,          # incrémenté à chaque chargement : les anciens threads s'arrêtent
    "prices_version": 0,    # change à chaque mise à jour des prix (la page se rafraîchit)
    "price_update": None,   # {"running", "done", "total", "error"}
    "warning": None,        # ex. collection affichée depuis le cache car le site est en erreur
}
_lock = threading.Lock()
price_cache = None if config.DEMO_MODE else PriceCache()


class Loading(Exception):
    """La collection est en cours de chargement (réponse 202 + progression)."""

EXPORT_FIELDS = [
    "id", "title", "rarity", "rarity_label", "category", "quantity", "avg_price", "median_price",
    "min_price", "max_price", "last_price", "nb_sales", "demand", "trend", "profit_score", "total_value",
    "price_source", "wiki_category", "atk", "def", "q_score", "pageviews", "is_shiny", "obtained_at", "wikipedia_url",
]


def _error(message, status):
    return jsonify({"error": message}), status


def _set_progress(step, done, total):
    state["progress"] = {"step": step, "done": done, "total": total}


def _publish(client, gen, cards):
    """Remplace la collection affichée si ce chargement est toujours le plus récent."""
    if state["client"] is client and state["load_gen"] == gen:
        state.update(cards=cards, loaded_at=time.strftime("%Y-%m-%d %H:%M:%S"), error=None)
        state["prices_version"] += 1


def _load_collection(client, gen):
    """1) Affichage immédiat avec les prix du cache, 2) prix réels manquants en arrière-plan."""
    start = time.time()
    try:
        _set_progress("collection", 0, None)
        state["warning"] = None
        try:
            raw = client.get_all_owned_cards()
            if price_cache is not None:
                price_cache.set_collection(raw)
                price_cache.save()
        except APIError as e:
            raw, saved_at = price_cache.get_collection() if price_cache is not None else (None, None)
            if not raw:
                raise
            logger.warning("Collection indisponible (%s) : affichage de la version du %s", e, saved_at)
            state["warning"] = (f"Le site WikiMasters renvoie une erreur ({e}). "
                                f"Collection affichée : dernière version connue du {saved_at}. "
                                "Réessaie « Rafraîchir » dans quelques minutes.")
        _set_progress("collection", len(raw), len(raw))
        cards = enrich_cards(client, raw, cache=price_cache, progress=_set_progress, fetch_sales=False)
        _publish(client, gen, cards)
        logger.info("Cartes affichées: %s en %.1fs", len(cards), time.time() - start)
    except Exception as e:
        logger.exception("Échec du chargement de la collection")
        state.update(error=e, loading=False, progress=None)
        return
    state.update(loading=False, progress=None)
    if price_cache is not None:
        threading.Thread(target=_update_prices, args=(client, gen, raw), daemon=True).start()


def _update_prices(client, gen, raw):
    """Récupère les prix réels manquants et met l'affichage à jour au fur et à mesure."""
    stop = lambda: state["load_gen"] != gen or state["client"] is not client
    update = {"running": True, "done": 0, "total": None, "error": None}
    state["price_update"] = update

    def progress(step, done, total):
        if step in ("sales", "sales_retry"):
            update.update(done=done, total=total, step=step)

    def on_batch():
        _publish(client, gen, enrich_cards(client, raw, cache=price_cache, fetch_sales=False))

    try:
        cards = enrich_cards(client, raw, cache=price_cache, progress=progress,
                             on_batch=on_batch, should_stop=stop)
        if not stop():
            _publish(client, gen, cards)
    except Exception as e:
        logger.exception("Échec de la mise à jour des prix")
        update["error"] = str(e)
    finally:
        update["running"] = False


def _get_cards(refresh=False):
    """Collection en mémoire ; lance le chargement en arrière-plan si besoin."""
    if state["client"] is None or not state["client"].authenticated:
        raise AuthenticationError("Non connecté")
    with _lock:
        if state["error"] is not None:
            error, state["error"] = state["error"], None
            raise error
        if not state["loading"] and (state["cards"] is None or refresh):
            state["load_gen"] += 1
            state.update(loading=True, progress={"step": "collection", "done": 0, "total": None})
            threading.Thread(target=_load_collection, args=(state["client"], state["load_gen"]),
                             daemon=True).start()
        if state["loading"]:
            raise Loading()
        return state["cards"]


def _public(card):
    """Carte sans l'historique complet (réponses plus légères)."""
    return {k: v for k, v in card.items() if k != "sales_history"}


def api_errors(view):
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Loading:
            return jsonify({"loading": True, "progress": state["progress"]}), 202
        except AuthenticationError as e:
            return _error(str(e) or "Authentification échouée", 401)
        except APIError as e:
            logger.error("Erreur API WikiMasters: %s", e)
            status = 503 if e.status_code in (None, 429, 502, 503) else 502
            return _error(f"Erreur WikiMasters: {e}", status)
    wrapper.__name__ = view.__name__
    return wrapper


def _static_version():
    """Date de modification des fichiers statiques (évite un CSS/JS périmé en cache navigateur)."""
    folder = os.path.join(app.root_path, "static")
    return int(max(os.path.getmtime(os.path.join(folder, f)) for f in os.listdir(folder)))


@app.route("/")
def index():
    return render_template("index.html", static_version=_static_version())


@app.route("/api/status")
def status():
    client = state["client"]
    return jsonify({
        "authenticated": bool(client and client.authenticated),
        "demo": config.DEMO_MODE,
        "email": (client.user or {}).get("email") if client and isinstance(client.user, dict) else None,
        "cards_loaded": len(state["cards"]) if state["cards"] is not None else 0,
        "loaded_at": state["loaded_at"],
        "has_credentials": bool(
            (config.WIKIMASTERS_EMAIL and config.WIKIMASTERS_PASSWORD) or config.WIKIMASTERS_REFRESH_TOKEN
        ),
        "loading": state["loading"],
        "price_update": state["price_update"],
        "warning": state["warning"],
        "prices_version": state["prices_version"],
        "cache": price_cache.stats() if price_cache else None,
        "has_sales": config.DEMO_MODE or bool(config.ENDPOINTS["sales_history"] or config.ENDPOINTS["market"]),
        "settings": {
            "min_sell_price": config.DEFAULT_MIN_SELL_PRICE,
            "min_buyers": config.DEFAULT_MIN_BUYERS,
        },
    })


@app.route("/api/login", methods=["POST"])
@api_errors
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email") or config.WIKIMASTERS_EMAIL
    password = data.get("password") or config.WIKIMASTERS_PASSWORD

    if config.DEMO_MODE:
        client = DemoClient()
    else:
        if not config._HAS_SESSION and (not email or not password):
            return _error("Session manquante : lance « python set_session.py » (voir README)", 400)
        client = WikiMastersClient(email, password)
    client.login()
    state.update(client=client, cards=None, loaded_at=None)
    return jsonify({"success": True, "demo": config.DEMO_MODE})


@app.route("/api/logout", methods=["POST"])
def logout():
    state.update(client=None, cards=None, loaded_at=None)
    return jsonify({"success": True})


@app.route("/api/cache/clear", methods=["POST"])
def clear_cache():
    if price_cache:
        price_cache.clear()
    return jsonify({"success": True})


@app.route("/api/collection")
@api_errors
def get_collection():
    refresh = request.args.get("refresh") in ("1", "true")
    cards = _get_cards(refresh=refresh)
    return jsonify({"cards": [_public(c) for c in cards], "total": len(cards), "loaded_at": state["loaded_at"]})


@app.route("/api/card/<card_id>")
@api_errors
def get_card(card_id):
    card = next((c for c in _get_cards() if c["id"] == card_id), None)
    if card is None:
        return _error("Carte introuvable", 404)
    return jsonify(card)


@app.route("/api/card/<card_id>/debug")
@api_errors
def debug_card(card_id):
    """Diagnostic du prix d'une carte, avec la session de l'app (remplace check_price.py)."""
    card = next((c for c in _get_cards() if c["id"] == card_id), None)
    if card is None:
        return _error("Carte introuvable", 404)
    client = state["client"]
    base_id = card_id.replace("-shiny", "")
    result = {"card": {k: card.get(k) for k in ("title", "rarity", "is_shiny", "starred", "wiki_category",
                                                 "avg_price", "price_source", "price_note")}}
    if config.ENDPOINTS.get("sales_history") and isinstance(client, WikiMastersClient):
        try:
            result["sales_response"] = client._request(
                "GET", client._url("sales_history", card_id=base_id), params=config.SALES_PARAMS)
        except Exception as e:
            result["sales_response"] = f"Erreur : {e}"
    market = price_cache.get_market() if price_cache else None
    result["market_auctions"] = [
        {"price": a.get("final_price") or a.get("effective_bid") or a.get("base_amount"),
         "has_bid": a.get("current_bid") is not None, "is_shiny": a.get("is_shiny"), "end_at": a.get("end_at")}
        for a in (market or []) if a.get("card_id") == base_id
    ]
    cached = price_cache.get_sales_entry(base_id) if price_cache else None
    result["price_cache"] = cached and {
        "averages": cached["averages"], "date": time.strftime("%d/%m %H:%M", time.localtime(cached["t"]))}
    return jsonify(result)


@app.route("/api/best-sellers")
@api_errors
def get_best_sellers():
    cards = _get_cards()
    min_price = request.args.get("min_price", config.DEFAULT_MIN_SELL_PRICE, type=float)
    max_price = request.args.get("max_price", type=float)
    min_buyers = request.args.get("min_buyers", config.DEFAULT_MIN_BUYERS, type=int)
    rarity = request.args.get("rarity")
    limit = request.args.get("limit", config.BEST_SELLERS_LIMIT, type=int)

    best = [c for c in cards if c["avg_price"] >= min_price and c["demand"] >= min_buyers and c["avg_price"] > 0]
    if max_price:
        best = [c for c in best if c["avg_price"] <= max_price]
    if rarity:
        best = [c for c in best if c["rarity"] == rarity]
    best.sort(key=lambda c: c["profit_score"], reverse=True)
    return jsonify({"cards": [_public(c) for c in best[:limit]], "total": len(best)})


@app.route("/api/categories")
@api_errors
def get_categories():
    groups = defaultdict(list)
    for card in _get_cards():
        groups[card["category"]].append(card)
    result = []
    for name, cards in groups.items():
        cards = sorted(cards, key=lambda c: c["avg_price"], reverse=True)
        result.append({
            "name": name,
            "count": sum(c["quantity"] for c in cards),
            "unique": len(cards),
            "total_value": round(sum(c["total_value"] for c in cards), 2),
            "top_cards": [_public(c) for c in cards[:5]],
        })
    result.sort(key=lambda g: g["total_value"], reverse=True)
    return jsonify({"categories": result})


@app.route("/api/related/<path:title>")
@api_errors
def get_related(title):
    related = CardAnalyzer.find_card_series(title, _get_cards())
    return jsonify({"title": title, "cards": [_public(c) for c in related[:20]]})


@app.route("/api/stats")
@api_errors
def get_stats():
    cards = _get_cards()
    rarity_counts = Counter()
    category_counts = Counter()
    category_values = Counter()
    for c in cards:
        rarity_counts[c["rarity"]] += c["quantity"]
        category_counts[c["category"]] += c["quantity"]
        category_values[c["category"]] += c["total_value"]
    most_valuable = max(cards, key=lambda c: c["avg_price"], default=None)
    return jsonify({
        "total_cards": sum(c["quantity"] for c in cards),
        "unique_cards": len(cards),
        "total_value": round(sum(c["total_value"] for c in cards), 2),
        "avg_card_value": round(sum(c["avg_price"] for c in cards) / len(cards), 2) if cards else 0,
        "by_rarity": {
            code: {"label": RARITY_LABELS[code], "count": rarity_counts.get(code, 0)}
            for code in RARITY_MULTIPLIERS
        },
        "by_category": {
            name: {"count": count, "value": round(category_values[name], 2)}
            for name, count in category_counts.most_common()
        },
        "most_valuable": _public(most_valuable) if most_valuable else None,
        "loaded_at": state["loaded_at"],
    })


@app.route("/api/export/csv")
@api_errors
def export_csv():
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS, extrasaction="ignore", delimiter=";")
    writer.writeheader()
    for card in _get_cards():
        writer.writerow(card)
    # BOM pour qu'Excel lise correctement les accents
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=wikimasters_collection.csv"},
    )


@app.route("/api/export/json")
@api_errors
def export_json():
    payload = json.dumps({"exported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cards": _get_cards()},
                         ensure_ascii=False, indent=2)
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=wikimasters_collection.json"},
    )


if __name__ == "__main__":
    if config.DEMO_MODE:
        logger.info("Mode DÉMO actif (aucun identifiant ou WIKIMASTERS_DEMO=1)")
    app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
