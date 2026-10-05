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
                   "current_bid", "listing_base_amount", "base_amount", "created_at", "settled_at", "end_at",
                   "status")


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
            self._data["catalog"] = data.get("catalog") or {}
            self._data["market_search"] = data.get("market_search") or {}
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
    def get_market(self, allow_stale=False):
        market = self._data["market"]
        if market and (allow_stale or time.time() - market["t"] < config.MARKET_CACHE_MINUTES * 60):
            return market["auctions"]
        return None

    def market_age_minutes(self):
        market = self._data["market"]
        return (time.time() - market["t"]) / 60 if market else None

    @staticmethod
    def _compact_auctions(auctions):
        compact = []
        for a in auctions:
            item = {k: a.get(k) for k in _AUCTION_FIELDS}
            card = a.get("card") or {}
            item["card"] = {"id": card.get("id"), "rarity": card.get("rarity"),
                            "category": card.get("category"), "is_shiny": card.get("is_shiny"),
                            "wikipedia_title": card.get("wikipedia_title"),
                            "wikipedia_url": card.get("wikipedia_url")}
            compact.append(item)
        return compact

    def set_market(self, auctions):
        compact = self._compact_auctions(auctions)
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
            self._data = {"sales": {}, "market": None, "collection": self._data.get("collection"), "catalog": {},
                          "market_search": {}}
        self.save()

    # --- Recherches d'une carte précise sur le Marché ---
    def get_market_search(self, query):
        entry = self._data.setdefault("market_search", {}).get(query.lower())
        if entry and time.time() - entry["t"] < config.MARKET_CACHE_MINUTES * 60:
            return entry["auctions"]
        return None

    def set_market_search(self, query, auctions):
        compact = self._compact_auctions(auctions)
        with self._lock:
            self._data.setdefault("market_search", {})[query.lower()] = {"t": time.time(), "auctions": compact}

    def searched_auctions(self):
        """Toutes les enchères trouvées par des recherches encore fraîches."""
        result = []
        for entry in list(self._data.setdefault("market_search", {}).values()):
            if time.time() - entry["t"] < config.MARKET_CACHE_MINUTES * 60:
                result.extend(entry["auctions"])
        return result

    # --- Recherches dans le catalogue complet ---
    def get_catalog(self, query):
        entry = self._data.setdefault("catalog", {}).get(query.lower())
        if entry and time.time() - entry["t"] < config.CATALOG_CACHE_HOURS * 3600:
            return entry["cards"]
        return None

    def set_catalog(self, query, cards):
        with self._lock:
            self._data.setdefault("catalog", {})[query.lower()] = {"t": time.time(), "cards": cards}

    def stats(self):
        market = self._data["market"]
        return {
            "sales_cached": len(self._data["sales"]),
            "market_cached": len(market["auctions"]) if market else 0,
            "market_age_min": round((time.time() - market["t"]) / 60) if market else None,
        }
