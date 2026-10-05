"""Bibliothèques : ensembles de cartes à compléter (ex. « Seconde guerre mondiale », « Pokémon »).

Une bibliothèque est définie par :
  - des mots-clés cherchés dans le titre ou la catégorie Wikipédia des cartes ;
  - et/ou une catégorie de l'app (ses mots-clés sont ajoutés) ;
  - et/ou une liste de titres précis ;
  - un filtre de rareté optionnel.
Elle est enregistrée dans libraries.json (données personnelles, non publiées).
"""
import json
import os
import re
import threading
import time
import uuid

import config
from wikimasters_api import THEMATIC_GROUPS, normalize_rarity, normalize_text

_lock = threading.Lock()


def _load():
    try:
        with open(config.LIBRARIES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(libraries):
    tmp = config.LIBRARIES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(libraries, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config.LIBRARIES_FILE)


def _clean(data):
    """Valide et normalise une bibliothèque envoyée par l'interface."""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Donne un nom à la bibliothèque")
    split = lambda value: [v.strip() for v in re.split(r"[,\n;]", value or "") if v.strip()] \
        if isinstance(value, str) else [str(v).strip() for v in (value or []) if str(v).strip()]
    category = str(data.get("category") or "").strip()
    if category and category not in THEMATIC_GROUPS:
        raise ValueError(f"Catégorie inconnue : {category}")
    library = {
        "name": name[:80],
        "keywords": split(data.get("keywords")),
        "category": category,
        "titles": split(data.get("titles")),
        "rarities": [normalize_rarity(r) for r in (data.get("rarities") or [])],
        "include_shiny": bool(data.get("include_shiny")),
    }
    if not (library["keywords"] or library["category"] or library["titles"]):
        raise ValueError("Ajoute au moins un mot-clé, une catégorie ou un titre")
    return library


def list_libraries():
    return _load()


def get_library(library_id):
    return next((lib for lib in _load() if lib["id"] == library_id), None)


def create_library(data):
    library = _clean(data)
    library.update(id=uuid.uuid4().hex[:12], created_at=time.strftime("%Y-%m-%d %H:%M"))
    with _lock:
        libraries = _load()
        libraries.append(library)
        _save(libraries)
    return library


def update_library(library_id, data):
    with _lock:
        libraries = _load()
        for i, lib in enumerate(libraries):
            if lib["id"] == library_id:
                libraries[i] = {**lib, **_clean(data)}
                _save(libraries)
                return libraries[i]
    return None


def delete_library(library_id):
    with _lock:
        libraries = _load()
        kept = [lib for lib in libraries if lib["id"] != library_id]
        _save(kept)
    return len(kept) != len(libraries)


class _Matcher:
    def __init__(self, library):
        keywords = list(library.get("keywords") or [])
        if library.get("category"):
            keywords += THEMATIC_GROUPS.get(library["category"], [])
        self.patterns = [re.compile(rf"\b{re.escape(normalize_text(k))}\b") for k in keywords if k]
        self.titles = {normalize_text(t) for t in library.get("titles") or []}
        self.rarities = set(library.get("rarities") or [])
        self.include_shiny = library.get("include_shiny", False)

    def matches(self, title, category):
        if normalize_text(title) in self.titles:
            return True
        text = normalize_text(f"{title} {category or ''}")
        return any(p.search(text) for p in self.patterns)

    def rarity_ok(self, rarity):
        return not self.rarities or rarity in self.rarities


def catalog_queries(library):
    """Recherches à lancer dans le catalogue complet pour cette bibliothèque."""
    queries = list(library.get("keywords") or []) + list(library.get("titles") or [])
    if library.get("category"):
        queries += THEMATIC_GROUPS.get(library["category"], [])
    seen, result = set(), []
    for q in queries:
        key = normalize_text(q).strip()
        if len(key) >= 3 and key not in seen:
            seen.add(key)
            result.append(q.strip())
    return result


def compute(library, cards, auctions, catalog_cards=None):
    """Cartes possédées, à acheter (marché) et à obtenir en paquets pour cette bibliothèque.

    cards : collection enrichie (app). auctions : enchères du marché (cache, format compact).
    catalog_cards : cartes trouvées dans le catalogue complet (recherches en cache).
    """
    m = _Matcher(library)

    owned = {}
    for card in cards:
        if card.get("is_shiny") and not m.include_shiny:
            continue
        if not m.rarity_ok(card["rarity"]) or not m.matches(card["title"].rstrip(" ✨"), card.get("wiki_category")):
            continue
        base_id = card["id"].replace("-shiny", "")
        owned.setdefault(base_id, card)
    owned_titles = {normalize_text(c["title"].rstrip(" ✨")) for c in owned.values()}

    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    to_buy = {}
    seen_auctions = set()
    for a in auctions or []:
        if a.get("id") in seen_auctions:
            continue
        seen_auctions.add(a.get("id"))
        # Enchères terminées ou réglées : le lien ne mènerait à rien d'achetable
        if a.get("status") not in (None, "active") or a.get("final_price") is not None:
            continue
        if a.get("end_at") and a["end_at"][:19] <= now:
            continue
        card = a.get("card") or {}
        title = card.get("wikipedia_title") or ""
        shiny = bool(a.get("is_shiny") or card.get("is_shiny"))
        rarity = normalize_rarity(a.get("snapshot_rarity") or card.get("rarity"))
        card_id = a.get("card_id") or card.get("id")
        if not title or card_id in owned or (shiny and not m.include_shiny):
            continue
        if not m.rarity_ok(rarity) or not m.matches(title, card.get("category")):
            continue
        # Prix à payer : l'enchère en cours, sinon le prix de départ
        price = a["current_bid"] if a.get("current_bid") is not None else (
            a.get("effective_bid") or a.get("listing_base_amount") or a.get("base_amount") or 0)
        offer = {"price": price, "end_at": a.get("end_at") or "", "has_bid": a.get("current_bid") is not None,
                 "auction_url": f"https://www.wiki-masters.com/marketplace/{a.get('id')}"}
        entry = to_buy.setdefault(card_id, {
            "card_id": card_id, "title": title, "rarity": rarity, "is_shiny": shiny,
            "wiki_category": card.get("category") or "", "wikipedia_url": card.get("wikipedia_url") or "",
            "offers": [],
        })
        entry["offers"].append(offer)

    for entry in to_buy.values():
        # La moins chère d'abord ; à prix égal, celle qui se termine le plus tôt
        entry["offers"].sort(key=lambda o: (o["price"], o["end_at"] or "9999"))
        cheapest = entry["offers"][0]
        entry.update(price=cheapest["price"], end_at=cheapest["end_at"], has_bid=cheapest["has_bid"],
                     auction_url=cheapest["auction_url"], listings=len(entry["offers"]))
        entry["offers"] = entry["offers"][:5]

    buy_titles = {normalize_text(c["title"]) for c in to_buy.values()}
    to_pack, pack_seen = [], set()
    for c in catalog_cards or []:
        key = normalize_text(c["title"])
        if (c["id"] in owned or c["id"] in to_buy or key in owned_titles or key in buy_titles
                or c["id"] in pack_seen or not m.rarity_ok(c["rarity"])
                or not m.matches(c["title"], c.get("category"))):
            continue
        pack_seen.add(c["id"])
        to_pack.append({**c, "source": "catalogue"})
    pack_titles = {normalize_text(c["title"]) for c in to_pack}
    to_pack += [{"title": t, "rarity": None, "category": "", "wikipedia_url": "", "source": "liste"}
                for t in library.get("titles") or []
                if normalize_text(t) not in owned_titles | buy_titles | pack_titles]
    rarity_rank = {"L": 0, "UR": 1, "SR": 2, "R": 3, "PC": 4, "C": 5}
    to_pack.sort(key=lambda c: (rarity_rank.get(c["rarity"], 9), c["title"]))

    to_buy_list = sorted(to_buy.values(), key=lambda c: c["price"])
    owned_list = sorted(owned.values(), key=lambda c: c["title"])
    total = len(owned_list) + len(to_buy_list) + len(to_pack)
    return {
        "library": library,
        "owned": owned_list,
        "to_buy": to_buy_list,
        "to_pack": to_pack,
        "stats": {
            "owned": len(owned_list),
            "missing": len(to_buy_list) + len(to_pack),
            "known_total": total,
            "progress": round(100 * len(owned_list) / total) if total else 0,
            "cost_to_complete": round(sum(c["price"] for c in to_buy_list), 2),
            "owned_value": round(sum(c.get("avg_price", 0) for c in owned_list), 2),
        },
    }
