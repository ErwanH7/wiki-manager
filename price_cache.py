"""Cache disque des prix (ventes par carte + enchères du marché).

Les moyennes de ventes bougent lentement : les garder quelques heures évite
de refaire une requête par carte à chaque chargement.
"""
import json
import logging
import os
import threading
import time

import config

logger = logging.getLogger(__name__)

# Champs d'enchère utiles à MarketIndex (le reste — vendeur, avatars… — n'est pas stocké)
_AUCTION_FIELDS = ("id", "card_id", "is_shiny", "snapshot_rarity", "final_price", "effective_bid",
                   "current_bid", "listing_base_amount", "base_amount", "created_at", "settled_at", "end_at")


class PriceCache:
    def __init__(self, path=None):
        self.path = path or config.PRICE_CACHE_FILE
        self._lock = threading.Lock()
        self._data = {"sales": {}, "market": None}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self._data["sales"] = data.get("sales") or {}
            self._data["market"] = data.get("market")
            self._data["collection"] = data.get("collection")
        except (OSError, ValueError):
            pass

    # --- Ventes par carte ---
    def get_sales(self, card_id):
        """Moyennes encore fraîches (None si absentes ou expirées)."""
        entry = self._data["sales"].get(card_id)
        if entry and time.time() - entry["t"] < config.SALES_CACHE_HOURS * 3600:
            return entry["averages"]
        return None

    def get_sales_entry(self, card_id):
        """Entrée complète, même expirée (repli quand le site ne répond pas)."""
        return self._data["sales"].get(card_id)

    def set_sales(self, card_id, averages, category=None):
        with self._lock:
            self._data["sales"][card_id] = {"t": time.time(), "averages": averages, "category": category}

    def all_sales(self):
        """[(card_id, catégorie, moyennes)] de toutes les ventes connues, pour les estimations."""
        return [(cid, e.get("category"), e["averages"])
                for cid, e in list(self._data["sales"].items()) if e["averages"]]

    # --- Enchères du marché ---
    def get_market(self):
        market = self._data["market"]
        if market and time.time() - market["t"] < config.MARKET_CACHE_MINUTES * 60:
            return market["auctions"]
        return None

    def set_market(self, auctions):
        compact = []
        for a in auctions:
            item = {k: a.get(k) for k in _AUCTION_FIELDS}
            card = a.get("card") or {}
            item["card"] = {"id": card.get("id"), "rarity": card.get("rarity"),
                            "category": card.get("category"), "is_shiny": card.get("is_shiny")}
            compact.append(item)
        with self._lock:
            self._data["market"] = {"t": time.time(), "auctions": compact}

    def save(self):
        with self._lock:
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False)
                os.replace(tmp, self.path)
            except OSError as e:
                logger.warning("Impossible d'écrire le cache des prix: %s", e)

    # --- Dernière collection lue (repli si le site est en panne) ---
    def set_collection(self, cards):
        with self._lock:
            self._data["collection"] = {"t": time.time(), "cards": cards}

    def get_collection(self):
        """(cartes, date lisible) de la dernière collection chargée, ou (None, None)."""
        saved = self._data.get("collection")
        if not saved:
            return None, None
        return saved["cards"], time.strftime("%d/%m %H:%M", time.localtime(saved["t"]))

    def clear(self):
        with self._lock:
            self._data = {"sales": {}, "market": None, "collection": self._data.get("collection")}
        self.save()

    def stats(self):
        market = self._data["market"]
        return {
            "sales_cached": len(self._data["sales"]),
            "market_cached": len(market["auctions"]) if market else 0,
            "market_age_min": round((time.time() - market["t"]) / 60) if market else None,
        }
