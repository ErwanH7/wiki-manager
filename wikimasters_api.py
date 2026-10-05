"""Client API WikiMasters + analyseur de cartes."""
import base64
import json
import logging
import random
import re
import statistics
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

import config

logger = logging.getLogger(__name__)

RARITY_MULTIPLIERS = {"C": 1, "PC": 2, "R": 3, "SR": 4, "UR": 5, "L": 6}
RARITY_LABELS = {
    "C": "Commun",
    "PC": "Peu Commun",
    "R": "Rare",
    "SR": "Super Rare",
    "UR": "Ultra Rare",
    "L": "Légende",
}
# Correspondances possibles renvoyées par l'API -> code interne
RARITY_ALIASES = {
    "c": "C", "commun": "C", "common": "C",
    "pc": "PC", "peu commun": "PC", "uncommon": "PC", "uc": "PC",
    "r": "R", "rare": "R",
    "sr": "SR", "super rare": "SR", "epic": "SR", "epique": "SR",
    "ur": "UR", "ultra rare": "UR", "ultra-rare": "UR",
    "l": "L", "legende": "L", "legendaire": "L", "legendary": "L", "legend": "L",
}

THEMATIC_GROUPS = {
    "porno": ["porno", "pornographique", "pornographie", "pornographiques", "hentai", "erotique",
              "lolicon", "film x", "industrie du sexe", "camgirl", "onlyfans", "strip-teaseuse"],
    "seconde guerre mondiale": [
        "seconde guerre mondiale", "deuxieme guerre mondiale", "nazi", "nazie", "nazis", "nazisme",
        "national-socialiste", "troisieme reich", "iiie reich", "reich", "hitler", "ss", "waffen-ss",
        "gestapo", "wehrmacht", "luftwaffe", "kriegsmarine", "panzer", "shoah", "holocauste",
        "camp de concentration", "camp d extermination", "regime de vichy", "collaborationniste",
        "resistant francais", "resistante francaise", "resistance francaise", "debarquement de normandie",
        "urss", "sovietique", "sovietiques", "union sovietique", "staline", "stalinien", "armee rouge",
        "nkvd", "goulag", "bolchevique", "communiste sovietique",
    ],
    "manga": ["manga", "anime", "one piece", "naruto", "dragon ball", "pokemon", "mangaka", "shonen"],
    "films": ["film", "cinema", "realisateur", "acteur", "actrice", "movie", "hollywood", "serie televisee", "comedien", "comedienne"],
    "personnages": ["personnage", "heros", "heroine", "character", "super-heros"],
    "lieux": ["ville", "pays", "monument", "chateau", "tour", "pont", "ile", "montagne", "fleuve", "paris", "commune", "village", "riviere", "lac", "eglise", "cathedrale", "quartier", "region", "departement", "station"],
    "histoire": ["guerre", "evenement", "siecle", "epoque", "revolution", "empire", "roi", "reine", "bataille", "navire", "militaire", "homme politique", "femme politique", "personnalite politique", "noble"],
    "science": ["physique", "chimie", "biologie", "espace", "planete", "atome", "scientifique", "mathematique", "mathematicien", "physicien", "chimiste", "medecin", "espece", "molecule", "maladie"],
    "sport": ["football", "rugby", "tennis", "equipe", "basket", "joueur", "olympique", "cycliste", "mbappe", "footballeur", "footballeuse", "club", "athlete", "sportif", "sportive", "handball", "boxeur", "pilote", "nageur", "nageuse", "entraineur"],
    "musique": ["chanteur", "chanteuse", "musicien", "groupe", "album", "rappeur", "compositeur", "chanson", "single", "musique", "guitariste", "pianiste"],
    "litterature": ["livre", "auteur", "poete", "roman", "ecrivain", "ecrivaine", "poesie", "journaliste", "philosophe", "bande dessinee"],
    "art": ["peintre", "peinture", "sculpture", "tableau", "artiste", "musee", "photographe", "architecte", "dessinateur"],
    "jeux-video": ["nintendo", "playstation", "video game", "jeu video", "console", "xbox"],
    "animaux": ["animal", "abeille", "chat", "chien", "oiseau", "lion", "insecte", "poisson", "mammifere", "reptile", "araignee", "papillon", "coleoptere"],
}

PRIORITY_GROUPS = ["porno", "seconde guerre mondiale"]

# Mots ignorés pour détecter les séries
_STOPWORDS = {
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "en", "au", "aux",
    "the", "of", "and", "a", "l", "d", "sur", "pour", "par",
}


class AuthenticationError(Exception):
    pass


class APIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def normalize_text(text):
    """Minuscules, sans accents."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def normalize_rarity(value):
    key = normalize_text(value).strip()
    return RARITY_ALIASES.get(key, "C")


def _first(data, *keys, default=None):
    """Renvoie la première clé présente (supporte les clés imbriquées 'a.b')."""
    for key in keys:
        current = data
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current is not None:
            return current
    return default


def _extract_list(payload):
    """Trouve la liste d'éléments dans une réponse JSON paginée ou non."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("cards", "items", "data", "results", "collection", "sales", "history"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _extract_list(value)
                if nested:
                    return nested
    return []


class WikiMastersClient:
    """Client HTTP pour l'API (non officielle) de WikiMasters."""

    def __init__(self, email, password, base_url=None):
        self.email = email
        self.password = password
        self.base_url = (base_url or config.API_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "WikiMastersCollectionManager/1.0",
        })
        self.authenticated = False
        self.user = None
        self.auth_session = None
        self._auth_lock = threading.Lock()
        self._auth_generation = 0  # incrémenté à chaque nouvelle session
        self._forbidden_refresh_tried = False

    def _uses_supabase(self):
        return bool(config.SUPABASE_URL and config.SUPABASE_ANON_KEY)

    def _refresh_auth(self, seen_generation=None):
        """Renouvelle la session (une seule fois même si plusieurs threads la demandent)."""
        with self._auth_lock:
            if seen_generation is not None and seen_generation != self._auth_generation:
                return  # un autre thread vient déjà de la renouveler
            logger.info("Renouvellement de la session WikiMasters")
            self._login_supabase()

    def _ensure_fresh_session(self):
        """Renouvelle le jeton d'accès (valide 1 h) un peu avant son expiration."""
        if not self._uses_supabase() or not self.auth_session:
            return
        expires_at = self.auth_session.get("expires_at") or 0
        if expires_at - time.time() < config.TOKEN_REFRESH_MARGIN:
            self._refresh_auth(self._auth_generation)

    def _url(self, endpoint_key, **params):
        return self.base_url + config.ENDPOINTS[endpoint_key].format(**params)

    def _request(self, method, url, retries=3, **kwargs):
        kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
        auth_retried = False
        self._ensure_fresh_session()
        for attempt in range(retries):
            generation = self._auth_generation
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise APIError(f"Erreur réseau: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = int(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                logger.warning("HTTP %s sur %s, nouvel essai dans %ss", response.status_code, url, wait)
                time.sleep(wait)
                continue
            if response.status_code in (401, 403):
                # Jeton expiré : on renouvelle et on rejoue. Un 403 n'est souvent pas lié à la session,
                # on ne tente le renouvellement qu'une fois pour toutes dans ce cas.
                may_refresh = response.status_code == 401 or not self._forbidden_refresh_tried
                if not auth_retried and may_refresh and self._uses_supabase() and self.auth_session:
                    if response.status_code == 403:
                        self._forbidden_refresh_tried = True
                    auth_retried = True
                    self._refresh_auth(generation)
                    response = self.session.request(method, url, **kwargs)
                if response.status_code == 403:
                    # Session valide mais accès refusé (limite du site, compte non Pro, anti-robot…)
                    detail = response.text[:200].strip()
                    raise APIError(f"HTTP 403 (accès refusé) : {detail or 'sans détail'}", 403)
                if response.status_code == 401:
                    self.authenticated = False
                    raise AuthenticationError(
                        f"Authentification échouée (HTTP {response.status_code}) : session expirée, "
                        "relance set_session.py avec un cookie récent")
            if response.status_code >= 400:
                raise APIError(f"HTTP {response.status_code} sur {url}", response.status_code)
            time.sleep(config.REQUEST_DELAY)
            try:
                return response.json()
            except ValueError as e:
                raise APIError(f"Réponse non JSON sur {url}") from e
        raise APIError(f"Échec après {retries} tentatives sur {url}")

    def login(self):
        if config.SUPABASE_URL and config.SUPABASE_ANON_KEY:
            return self._login_supabase()
        payload = self._request(
            "POST", self._url("login"),
            json={"email": self.email, "password": self.password},
        )
        token = _first(payload or {}, "token", "access_token", "accessToken", "data.token")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.user = _first(payload or {}, "user", "data.user", default={"email": self.email})
        self.authenticated = True
        logger.info("Connecté à WikiMasters en tant que %s", self.email)
        return True

    def _load_refresh_token(self):
        try:
            with open(config.SESSION_FILE, encoding="utf-8") as f:
                token = json.load(f).get("refresh_token")
                if token:
                    return token
        except (OSError, ValueError):
            pass
        return config.WIKIMASTERS_REFRESH_TOKEN

    @staticmethod
    def _save_session(payload):
        try:
            with open(config.SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump({"refresh_token": payload["refresh_token"],
                           "expires_at": payload.get("expires_at")}, f)
        except OSError as e:
            logger.warning("Impossible de sauvegarder la session: %s", e)

    def _set_session_cookie(self, payload):
        """Cookie de session au format @supabase/ssr, lu par les routes /api du site."""
        project_ref = config.SUPABASE_URL.split("//")[1].split(".")[0]
        name = f"sb-{project_ref}-auth-token"
        session = {k: payload[k] for k in
                   ("access_token", "token_type", "expires_in", "expires_at", "refresh_token", "user")
                   if k in payload}
        raw = json.dumps(session, separators=(",", ":")).encode()
        value = "base64-" + base64.urlsafe_b64encode(raw).decode().rstrip("=")
        domain = requests.utils.urlparse(self.base_url).hostname
        for i in range(0, 10):
            self.session.cookies.set(f"{name}.{i}", None, domain=domain)
        for i, start in enumerate(range(0, len(value), 3180)):
            self.session.cookies.set(f"{name}.{i}", value[start:start + 3180], domain=domain, path="/")

    def _login_supabase(self):
        """Connexion via Supabase Auth (utilisé par wiki-masters.com).

        Priorité au refresh token (pas de captcha), sinon email/mot de passe.
        """
        self.session.headers["apikey"] = config.SUPABASE_ANON_KEY
        refresh_token = self._load_refresh_token()
        if refresh_token:
            grant, body = "refresh_token", {"refresh_token": refresh_token}
        else:
            grant, body = "password", {"email": self.email, "password": self.password}
        try:
            response = self.session.post(
                f"{config.SUPABASE_URL}/auth/v1/token",
                params={"grant_type": grant},
                json=body,
                timeout=config.REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise APIError(f"Erreur réseau: {e}") from e
        if response.status_code in (400, 401, 403):
            try:
                detail = response.json()
            except ValueError:
                detail = {}
            message = detail.get("error_description") or detail.get("msg") or detail.get("message") or ""
            if grant == "refresh_token":
                message += " - session expirée : recopie un nouveau refresh token depuis le navigateur (voir .env)"
            elif "captcha" in message:
                message += " - renseigne WIKIMASTERS_REFRESH_TOKEN dans .env (voir .env)"
            raise AuthenticationError(f"Authentification échouée : {message}".strip())
        if response.status_code >= 400:
            raise APIError(f"HTTP {response.status_code} sur Supabase Auth", response.status_code)
        payload = response.json()
        self.session.headers["Authorization"] = f"Bearer {payload['access_token']}"
        self._save_session(payload)
        if not payload.get("expires_at") and payload.get("expires_in"):
            payload["expires_at"] = int(time.time()) + int(payload["expires_in"])
        self.auth_session = payload
        self._auth_generation += 1
        self._set_session_cookie(payload)
        self.user = payload.get("user") or {"email": self.email}
        self.authenticated = True
        logger.info("Connecté à WikiMasters (Supabase) en tant que %s, user id %s",
                    self.email, self.user.get("id"))
        return True

    def get_all_owned_cards(self):
        """Récupère toutes les cartes possédées (pagination automatique)."""
        if not self.authenticated:
            self.login()
        cards, seen = [], set()
        page = config.PAGE_START
        first_page_size = None
        for _ in range(config.MAX_PAGES):
            if config.PAGINATION == "offset":
                params = {"offset": (page - config.PAGE_START) * config.PAGE_SIZE, "limit": config.PAGE_SIZE}
            else:
                params = {"page": page}
            params.update(config.OWNED_EXTRA_PARAMS)
            try:
                payload = self._request("GET", self._url("owned_cards"), params=params, retries=4)
            except APIError as e:
                raise APIError(f"{e} (page {page} de la collection, {len(cards)} cartes déjà lues)",
                               e.status_code) from e
            items = _extract_list(payload)
            new = [c for c in (self._normalize_card(i) for i in items) if c["entry_id"] not in seen]
            seen.update(c["entry_id"] for c in new)
            cards.extend(new)
            logger.info("Page %s: %s cartes (total %s)", page, len(items), len(cards))
            has_more = _first(payload, "hasMore", "has_more", "pagination.hasMore") if isinstance(payload, dict) else None
            if not new or has_more is False:
                break
            if first_page_size is None:
                first_page_size = len(items)
            elif len(items) < first_page_size:
                break  # page incomplète = dernière page, inutile de demander la suivante
            page += 1
        return cards

    def get_market_auctions(self, progress=None):
        """Toutes les enchères visibles sur le marché (/api/marketplace, pages à partir de 1)."""
        if not self.authenticated:
            self.login()
        def fetch(page):
            payload = self._request("GET", self._url("market"), params={
                "page": page, "limit": config.MARKET_PAGE_SIZE, "sort": config.MARKET_SORT,
            })
            return payload.get("auctions", []) if isinstance(payload, dict) else _extract_list(payload)

        auctions, seen = [], set()
        page, done = 1, False
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
            while not done and page <= config.MARKET_MAX_PAGES:
                batch = range(page, min(page + config.MAX_WORKERS, config.MARKET_MAX_PAGES + 1))
                for items in pool.map(fetch, batch):
                    new = [a for a in items if a.get("id") not in seen]
                    seen.update(a.get("id") for a in new)
                    auctions.extend(new)
                    if not new or len(items) < config.MARKET_PAGE_SIZE:
                        done = True
                page += len(batch)
                if progress:
                    progress("market", len(auctions), None)
        logger.info("Marché: %s enchères chargées", len(auctions))
        return auctions

    def get_market_index(self, cache=None, progress=None):
        if not config.ENDPOINTS.get("market"):
            return None
        auctions = cache.get_market() if cache else None
        if auctions is None:
            auctions = self.get_market_auctions(progress)
            if cache:
                cache.set_market(auctions)
        else:
            logger.info("Marché: %s enchères depuis le cache", len(auctions))
        return MarketIndex(auctions)

    def get_card_sales_history(self, card_id):
        if not config.ENDPOINTS["sales_history"]:
            return {"sales": [], "demand": None}
        payload = self._request("GET", self._url("sales_history", card_id=card_id),
                                params=config.SALES_PARAMS, retries=5)
        if isinstance(payload, dict) and isinstance(payload.get("summary"), dict):
            # {"summary": {"PC": {"average": 5}, "R": {"average": 6}}} : moyenne par rareté
            averages = {}
            for rarity, data in payload["summary"].items():
                avg = _first(data, "average", "avg") if isinstance(data, dict) else data
                if isinstance(avg, (int, float)) and avg > 0:
                    averages[normalize_rarity(rarity)] = float(avg)
            return {"sales": [], "demand": None, "averages": averages}
        sales = []
        for item in _extract_list(payload):
            price = _first(item, "price", "amount", "value")
            if price is None:
                continue
            sales.append({
                "price": float(price),
                "date": _first(item, "date", "createdAt", "created_at", "soldAt", default=""),
            })
        sales.sort(key=lambda s: s["date"])
        demand = _first(payload, "buyers", "demand", "interested", "buyOrders") if isinstance(payload, dict) else None
        return {"sales": sales, "demand": int(demand) if isinstance(demand, (int, float)) else None}

    @staticmethod
    def _normalize_card(raw):
        card = _first(raw, "card", default=raw)
        card_id = str(_first(card, "id", "_id", "cardId", default=_first(raw, "card_id", "id", default="")))
        return {
            "id": card_id,
            # Identifiant de l'exemplaire possédé (une carte shiny et normale sont 2 entrées)
            "entry_id": str(_first(raw, "id", default=card_id)),
            "title": _first(card, "wikipedia_title", "title", "name", "titre", default="Sans titre"),
            "rarity": normalize_rarity(_first(raw, "rarity", "rarete", "card.rarity", default="C")),
            "quantity": int(_first(raw, "quantity", "count", "qty", default=1) or 1),
            "image": _first(card, "image_url", "image", "imageUrl", default="") or "",
            # Catégorie Wikipédia de la carte (ex. "actrice française")
            "description": _first(card, "category", "description", "extract", "summary", default="") or "",
            "wiki_category": _first(card, "category", default="") or "",
            "wikipedia_url": _first(card, "wikipedia_url", default="") or "",
            "atk": _first(card, "atk", default=0),
            "def": _first(card, "def", default=0),
            "q_score": _first(card, "q_score", default=0),
            "pageviews": _first(card, "pageviews", default=0),
            "is_shiny": bool(_first(raw, "is_shiny", default=False)),
            "obtained_at": _first(raw, "obtained_at", default=""),
            "starred": bool(_first(raw, "starred", default=False)),
            "tags": [t.get("name") for t in (raw.get("tags") or []) if isinstance(t, dict) and t.get("name")],
        }


def _auction_price(auction):
    """Prix d'une enchère : prix final si vendue, sinon enchère/prix effectif."""
    for key in ("final_price", "effective_bid", "current_bid", "listing_base_amount", "base_amount"):
        value = auction.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def _interquartile(values):
    """Retire les extrêmes (annonces farfelues) pour l'estimation par rareté."""
    values = sorted(values)
    if len(values) < 8:
        return values
    q = len(values) // 4
    return values[q:len(values) - q]


# Mots ignorés pour comparer les catégories Wikipédia ("actrice française" ~ "actrice américaine")
_CATEGORY_NOISE = {
    "francais", "francaise", "americain", "americaine", "britannique", "anglais", "anglaise",
    "allemand", "allemande", "italien", "italienne", "espagnol", "espagnole", "belge", "suisse",
    "canadien", "canadienne", "japonais", "japonaise", "russe", "chinois", "chinoise", "ancien",
    "ancienne", "sorti", "sortie", "en", "dans", "ne", "nee", "france", "etats", "unis",
}
MIN_CATEGORY_SAMPLES = 3


def category_tokens(category):
    words = re.findall(r"[a-z]+", normalize_text(category))
    return [w for w in words if w not in _STOPWORDS and w not in _CATEGORY_NOISE and len(w) > 2]


class MarketIndex:
    """Prix du marché par carte, et estimation par type de carte en repli."""

    def __init__(self, auctions):
        self.by_card = {}
        self.by_head = {}
        for a in auctions:
            price = _auction_price(a)
            if price is None:
                continue
            card = a.get("card") or {}
            shiny = bool(a.get("is_shiny") or card.get("is_shiny"))
            entry = {
                "price": price,
                "date": a.get("settled_at") or a.get("created_at") or "",
                "sold": a.get("final_price") is not None,
                "has_bid": a.get("current_bid") is not None,
            }
            self.by_card.setdefault((a.get("card_id") or card.get("id"), shiny), []).append(entry)
            tokens = category_tokens(card.get("category", ""))
            if tokens and not shiny:
                self.by_head.setdefault(tokens[0], []).append((set(tokens), price))

    def history_for(self, card):
        shiny = bool(card.get("is_shiny"))
        entries = sorted(self.by_card.get((card["id"], shiny), []), key=lambda e: e["date"])
        if entries:
            demand = sum(1 for e in entries if e["sold"] or e["has_bid"])
            return {"sales": [{"price": e["price"], "date": e["date"]} for e in entries],
                    "demand": demand, "source": "card", "bids": demand}
        prices = [] if shiny else self.similar_prices(card.get("wiki_category", ""))
        if prices:
            return {"sales": [{"price": p, "date": ""} for p in prices], "demand": 0, "source": "category"}
        return {"sales": [], "demand": 0, "source": None}

    def similar_prices(self, category):
        """Prix des enchères de cartes du même type (même mot principal de catégorie)."""
        tokens = category_tokens(category)
        if not tokens:
            return []
        candidates = self.by_head.get(tokens[0], [])
        # Plus précis si assez d'exemples partagent 2 mots ("jeu vidéo", "club football")
        closer = [p for t, p in candidates if len(t & set(tokens)) >= 2]
        prices = closer if len(closer) >= MIN_CATEGORY_SAMPLES else [p for _, p in candidates]
        return _interquartile(prices) if len(prices) >= MIN_CATEGORY_SAMPLES else []


class DemoClient:
    """Faux client avec une collection générée, pour tester sans compte."""

    TITLES = [
        "One Piece", "Naruto", "Dragon Ball", "Kylian Mbappé", "Zinédine Zidane", "Tour Eiffel",
        "Château de Versailles", "Napoléon Bonaparte", "Révolution française", "Albert Einstein",
        "Marie Curie", "Système solaire", "Les Misérables", "Victor Hugo", "La Joconde",
        "Vincent van Gogh", "Daft Punk", "Édith Piaf", "Star Wars", "Le Seigneur des anneaux (film)",
        "Abeille", "Lion", "Super Mario", "Nintendo Switch", "Rugby à XV", "Roland-Garros (tennis)",
        "Mont Blanc", "Paris", "Pokémon", "Batman (personnage)", "Molière", "Claude Monet",
        "Première Guerre mondiale", "Station spatiale internationale", "Stromae", "Titanic (film)",
        "Naruto Uzumaki", "One Piece Film Red", "Dragon Ball Z", "Jeux olympiques de Paris 2024",
    ]

    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self.authenticated = False
        self.user = {"email": "demo@wikimasters.local"}
        self._sales = {}

    def login(self):
        self.authenticated = True
        return True

    def get_all_owned_cards(self):
        rarities = ["C"] * 10 + ["PC"] * 6 + ["R"] * 4 + ["SR"] * 2 + ["UR", "L"]
        cards = []
        for i, title in enumerate(self.TITLES):
            cards.append({
                "id": str(1000 + i),
                "title": title,
                "rarity": self.rng.choice(rarities),
                "quantity": self.rng.choice([1, 1, 1, 2, 3]),
                "image": "",
                "description": "",
            })
        return cards

    def get_card_sales_history(self, card_id):
        if card_id not in self._sales:
            rng = random.Random(card_id)
            base = rng.uniform(5, 400)
            now = datetime.now()
            sales = []
            for d in range(rng.randint(0, 25)):
                base = max(1, base * rng.uniform(0.85, 1.18))
                sales.append({
                    "price": round(base, 2),
                    "date": (now - timedelta(days=60 - d * 2)).isoformat(timespec="seconds"),
                })
            self._sales[card_id] = {"sales": sales, "demand": rng.randint(0, 30)}
        return self._sales[card_id]


class CardAnalyzer:
    """Calculs de valeur, catégorisation et séries."""

    @staticmethod
    def categorize_card(title, description=""):
        text = normalize_text(f"{title} {description}")
        # Catégories prioritaires : un seul mot-clé suffit (ex. "actrice pornographique" -> porno)
        for category in PRIORITY_GROUPS:
            if any(re.search(rf"\b{re.escape(normalize_text(kw))}\b", text) for kw in THEMATIC_GROUPS[category]):
                return category
        best, best_hits = "autre", 0
        for category, keywords in THEMATIC_GROUPS.items():
            hits = sum(1 for kw in keywords if re.search(rf"\b{re.escape(normalize_text(kw))}\b", text))
            if hits > best_hits:
                best, best_hits = category, hits
        return best

    @staticmethod
    def calculate_card_value(card, sales_history):
        sales = (sales_history or {}).get("sales", [])
        prices = [s["price"] for s in sales]
        demand = (sales_history or {}).get("demand")
        if demand is None:
            demand = len(prices)
        multiplier = RARITY_MULTIPLIERS.get(card.get("rarity"), 1)

        value = {
            "avg_price": round(statistics.mean(prices), 2) if prices else 0.0,
            "median_price": round(statistics.median(prices), 2) if prices else 0.0,
            "min_price": round(min(prices), 2) if prices else 0.0,
            "max_price": round(max(prices), 2) if prices else 0.0,
            "last_price": round(prices[-1], 2) if prices else 0.0,
            "nb_sales": len(prices),
            "demand": demand,
            "trend": "stable",
            "sales_history": sales,
        }
        value["profit_score"] = round(value["avg_price"] * max(demand, 1) * multiplier / 100, 2)

        if (sales_history or {}).get("source") == "category":
            value["avg_price"] = value["median_price"]
            value["last_price"] = value["median_price"]
            value["nb_sales"] = 0
            value["sales_history"] = []
            value["profit_score"] = round(value["avg_price"] * multiplier / 100, 2)
            return value

        if len(prices) >= 10:
            recent, old = prices[-5:], prices[-10:-5]
            if sum(recent) > sum(old) * 1.05:
                value["trend"] = "up"
            elif sum(recent) < sum(old) * 0.95:
                value["trend"] = "down"
        return value

    @staticmethod
    def title_tokens(title):
        words = re.findall(r"[a-z0-9]+", normalize_text(re.sub(r"\(.*?\)", "", title)))
        return {w for w in words if w not in _STOPWORDS and len(w) > 2}

    @staticmethod
    def find_card_series(title, cards):
        """Cartes de la même série : mots-clés du titre en commun, puis même catégorie."""
        tokens = CardAnalyzer.title_tokens(title)
        source = next((c for c in cards if c["title"] == title), None)
        scored = []
        for card in cards:
            if card["title"] == title:
                continue
            common = tokens & CardAnalyzer.title_tokens(card["title"])
            score = len(common) * 10
            if source and card.get("category") == source.get("category") and source.get("category") != "autre":
                score += 1
            if score:
                scored.append((score, card))
        scored.sort(key=lambda x: (-x[0], -x[1].get("avg_price", 0)))
        return [card for _, card in scored]


class SalesEstimator:
    """Estimation à partir de VENTES RÉELLES d'autres cartes (plus fiable que les prix demandés)."""

    def __init__(self, entries):
        # entries : [(catégorie wiki, {rareté: moyenne})]
        self.by_head = {}
        self.by_rarity = {}
        for category, averages in entries:
            tokens = category_tokens(category or "")
            for rarity, avg in (averages or {}).items():
                self.by_rarity.setdefault(rarity, []).append(avg)
                if tokens:
                    self.by_head.setdefault(tokens[0], []).append((set(tokens), rarity, avg))

    def estimate(self, category, rarity):
        """(prix, explication) ou (None, None)."""
        tokens = category_tokens(category or "")
        if tokens:
            candidates = self.by_head.get(tokens[0], [])
            closer = [avg for t, _, avg in candidates if len(t & set(tokens)) >= 2]
            for prices, label in ((closer, "même type"), ([avg for _, _, avg in candidates], "type proche")):
                if len(prices) >= config.MIN_SALES_SAMPLES:
                    return statistics.median(prices), f"ventes de {len(prices)} cartes du {label}"
        prices = self.by_rarity.get(rarity, [])
        if len(prices) >= config.MIN_SALES_SAMPLES:
            return statistics.median(prices), f"ventes de {len(prices)} cartes de même rareté"
        return None, None


def _lower_quartile(values):
    values = sorted(values)
    return values[(len(values) - 1) // 4] if values else None


def _set_price(enriched, price, source, note=None):
    """Fixe le prix retenu et recalcule score / valeur."""
    enriched["avg_price"] = round(price, 2)
    if source != "card":
        for key in ("median_price", "min_price", "max_price", "last_price"):
            enriched[key] = enriched["avg_price"]
        enriched["sales_history"] = []
    enriched["price_source"] = source
    if note:
        enriched["price_note"] = note
    multiplier = RARITY_MULTIPLIERS.get(enriched["rarity"], 1)
    enriched["profit_score"] = round(enriched["avg_price"] * max(enriched["demand"], 1) * multiplier / 100, 2)


def enrich_cards(client, cards, max_workers=None, cache=None, progress=None,
                 fetch_sales=True, on_batch=None, should_stop=None):
    """Ajoute catégorie + prix à chaque carte.

    Ordre de fidélité du prix :
      1. ventes réelles de la carte (sa rareté)      4. enchères en cours de la carte (prix le plus bas)
      2. ventes réelles en cache, même anciennes     5. ≈ ventes réelles de cartes similaires
      3. ventes réelles de la carte (autre rareté)   6. ≈ prix demandés de cartes du même type (bas)
    cache : PriceCache optionnel. progress : callback(étape, fait, total).
    fetch_sales=False : aucune requête de ventes, uniquement le cache (affichage immédiat).
    on_batch : appelé régulièrement pendant la récupération des ventes (mise à jour progressive).
    should_stop : callable, True pour interrompre la récupération (nouveau chargement lancé).
    """
    should_stop = should_stop or (lambda: False)
    progress = progress or (lambda *args: None)
    market = None
    if hasattr(client, "get_market_index"):
        try:
            progress("market", 0, None)
            market = client.get_market_index(cache=cache, progress=progress)
        except AuthenticationError:
            raise
        except Exception as e:
            logger.warning("Marché indisponible: %s", e)

    use_sales = bool(config.ENDPOINTS.get("sales_history")) and market is not None
    summaries = {}       # card_id -> {rareté: moyenne} (frais)
    stale = {}           # card_id -> (moyennes, date) depuis un cache expiré
    sales_errors = {}
    skipped_starred, pending = set(), set()
    lock = threading.Lock()
    throttle = {"forbidden": 0, "pauses": 0, "until": 0.0, "stopped": False}

    def fetch_one(card):
        cid = card["id"]
        if should_stop():
            return
        wait = throttle["until"] - time.time()
        if wait > 0:
            time.sleep(wait)
        if throttle["stopped"]:
            sales_errors[cid] = "non demandé : le site refuse l'accès (HTTP 403)"
            return
        try:
            averages = client.get_card_sales_history(cid).get("averages", {})
        except AuthenticationError:
            raise
        except Exception as e:
            logger.warning("Ventes indisponibles pour %s: %s", card["title"], e)
            sales_errors[cid] = str(e)
            if getattr(e, "status_code", None) in (403, 429):
                with lock:
                    throttle["forbidden"] += 1
                    if throttle["forbidden"] >= config.SALES_MAX_FORBIDDEN and time.time() >= throttle["until"]:
                        throttle["forbidden"] = 0
                        if throttle["pauses"] >= config.SALES_MAX_PAUSES:
                            throttle["stopped"] = True
                            logger.warning("Le site refuse toujours les ventes : arrêt pour ce chargement")
                        else:
                            throttle["pauses"] += 1
                            delay = config.SALES_PAUSE_SECONDS * throttle["pauses"]
                            throttle["until"] = time.time() + delay
                            logger.warning("Le site freine les requêtes (HTTP %s) : pause de %ss",
                                           e.status_code, delay)
            return
        with lock:
            throttle["forbidden"] = 0
        summaries[cid] = averages
        sales_errors.pop(cid, None)
        if cache:
            cache.set_sales(cid, averages, category=card.get("wiki_category"))

    workers = max_workers or config.MAX_WORKERS
    if use_sales:
        unique = list({c["id"]: c for c in cards}.values())
        to_fetch = []
        for card in unique:
            cached = cache.get_sales(card["id"]) if cache else None
            if cached is not None:
                summaries[card["id"]] = cached
            elif config.SKIP_STARRED_PRICES and card.get("starred"):
                skipped_starred.add(card["id"])
            else:
                to_fetch.append(card)
        if not fetch_sales:
            pending.update(c["id"] for c in to_fetch)
            to_fetch = []
        else:
            logger.info("Ventes: %s en cache, %s favoris ignorés, %s à charger",
                        len(summaries), len(skipped_starred), len(to_fetch))
        progress("sales", 0, len(to_fetch))
        with ThreadPoolExecutor(max_workers=min(workers, config.SALES_WORKERS)) as pool:
            for i, _ in enumerate(pool.map(fetch_one, to_fetch), 1):
                if i % 10 == 0 or i == len(to_fetch):
                    progress("sales", i, len(to_fetch))
                if i % config.PRICE_BATCH == 0 and i < len(to_fetch):
                    if cache:
                        cache.save()  # un chargement interrompu ne repart pas de zéro
                    if on_batch:
                        on_batch()
        failed = [c for c in to_fetch if c["id"] in sales_errors]
        if failed and not throttle["stopped"] and not should_stop():
            logger.info("Nouvel essai des ventes pour %s cartes, une par une", len(failed))
            time.sleep(3)
            for i, card in enumerate(failed, 1):
                if should_stop():
                    break
                fetch_one(card)
                progress("sales_retry", i, len(failed))
                time.sleep(1)
        if sales_errors:
            logger.warning("Ventes indisponibles pour %s cartes", len(sales_errors))
        for cid in {c["id"] for c in unique} - set(summaries):
            entry = cache.get_sales_entry(cid) if cache else None
            if entry:
                stale[cid] = (entry["averages"], time.strftime("%d/%m %H:%M", time.localtime(entry["t"])))
        if cache:
            cache.save()

    # Références de ventes réelles pour les estimations : tout le cache + ce chargement
    # (les entrées de cache sans catégorie la récupèrent depuis la collection)
    category_by_id = {c["id"]: c.get("wiki_category") for c in cards}
    entries = [(category or category_by_id.get(cid), averages)
               for cid, category, averages in (cache.all_sales() if cache else [])]
    entries += [(c.get("wiki_category"), summaries[c["id"]]) for c in cards
                if c["id"] in summaries and not (cache and cache.get_sales_entry(c["id"]))]
    estimator = SalesEstimator(entries)

    def pick_average(averages, rarity):
        if rarity in averages:
            return averages[rarity], True
        if averages:
            return sum(averages.values()) / len(averages), False
        return None, False

    def enrich(card):
        if market is not None:
            history = market.history_for(card)
        else:
            try:
                history = client.get_card_sales_history(card["id"])
            except AuthenticationError:
                raise
            except Exception as e:  # une carte en erreur ne bloque pas les autres
                logger.warning("Historique indisponible pour %s: %s", card["title"], e)
                history = {"sales": [], "demand": None}
        enriched = dict(card)
        if card.get("is_shiny"):
            enriched["id"] = f"{card['id']}-shiny"
            enriched["title"] = f"{card['title']} ✨"
        enriched["category"] = CardAnalyzer.categorize_card(card["title"], card.get("description", ""))
        enriched["rarity_label"] = RARITY_LABELS.get(card["rarity"], card["rarity"])
        enriched.update(CardAnalyzer.calculate_card_value(card, history))
        source = history.get("source", "card" if history.get("sales") else None)
        enriched["price_source"] = source
        if not use_sales:
            enriched["total_value"] = round(enriched["avg_price"] * enriched["quantity"], 2)
            return enriched

        cid, shiny = card["id"], bool(card.get("is_shiny"))
        error = sales_errors.get(cid)
        avg, exact = pick_average(summaries.get(cid, {}), card["rarity"])
        old_avg, old_exact = pick_average(stale[cid][0], card["rarity"]) if cid in stale else (None, False)

        # Prix issu des ventes réelles : fraîches > cache ancien ; rareté exacte > autre rareté
        sales_choice = None
        if avg and (exact or not old_exact):
            pick = (avg, exact, None)
        elif old_avg:
            pick = (old_avg, old_exact, f"Prix réel du cache ({stale[cid][1]}), le site n'a pas répondu cette fois")
        elif avg:
            pick = (avg, False, None)
        else:
            pick = None
        if pick:
            value, is_exact, note = pick
            is_exact = is_exact and not shiny
            sales_choice = (value, "sales" if is_exact else "sales_other", note)
            if not is_exact and not shiny:
                # Moyenne d'une AUTRE rareté : retenue seulement si cohérente avec les cartes similaires
                estimate, why = estimator.estimate(card.get("wiki_category"), card["rarity"])
                if estimate and value > config.MAX_ASK_RATIO * estimate:
                    sales_choice = (estimate, "estimate_sales",
                                    f"Estimation : {why}. Vente moyenne de {value:g} dans une autre rareté "
                                    "ignorée (incohérente avec les cartes similaires)")

        if shiny and source == "card":
            # Le résumé des ventes ne distingue pas les shiny : leurs propres enchères priment
            _set_price(enriched, min(s["price"] for s in history["sales"]), "card")
        elif sales_choice:
            _set_price(enriched, *sales_choice)
        elif source == "card":
            # Prix demandés : le plus bas est le plus proche d'un vrai prix de vente
            ask = min(s["price"] for s in history["sales"])
            estimate, why = estimator.estimate(card.get("wiki_category"), card["rarity"])
            if not history.get("bids") and estimate and ask > config.MAX_ASK_RATIO * estimate:
                _set_price(enriched, estimate, "estimate_sales",
                           f"Estimation : {why} (enchère à {ask:g} sans aucune offre ignorée)")
            else:
                _set_price(enriched, ask, "card",
                           None if history.get("bids") else "Prix demandé, sans offre pour l'instant")
        else:
            estimate, why = (None, None) if shiny else estimator.estimate(card.get("wiki_category"), card["rarity"])
            if estimate:
                _set_price(enriched, estimate, "estimate_sales", f"Estimation : {why}")
            elif source == "category":
                low = _lower_quartile([s["price"] for s in history["sales"]])
                _set_price(enriched, low, "category",
                           "Estimation peu fiable : prix demandés (les plus bas) de cartes du même type")
            else:
                enriched.update(avg_price=0.0, price_source=None, profit_score=0.0)
        if enriched.get("price_source") != "sales":
            if cid in skipped_starred:
                enriched["price_note"] = ("Favori : prix réel non recherché. " + enriched.get("price_note", "")).strip()
            elif cid in pending:
                enriched["price_note"] = ("Prix réel en cours de récupération. " + enriched.get("price_note", "")).strip()
        if error and enriched.get("price_source") not in ("sales",):
            enriched["price_note"] = (enriched.get("price_note", "") + f" | Lecture des ventes échouée : {error}").strip(" |")
        enriched["total_value"] = round(enriched["avg_price"] * enriched["quantity"], 2)
        return enriched

    progress("prices", 0, len(cards))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(enrich, cards))
