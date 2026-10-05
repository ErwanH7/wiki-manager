"""Configuration de WikiMasters Collection Manager.

Les valeurs sont lues depuis le fichier .env (voir .env.example).
Les endpoints de l'API WikiMasters ne sont pas officiels : ajuste-les ici
(ou via .env) si le site change.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# Identifiants
WIKIMASTERS_EMAIL = os.getenv("WIKIMASTERS_EMAIL", "")
WIKIMASTERS_PASSWORD = os.getenv("WIKIMASTERS_PASSWORD", "")

# Serveur Flask
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = _bool("FLASK_DEBUG", False)
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")

# Mode démo : données générées localement, aucune requête vers WikiMasters.
# Activé automatiquement si aucun identifiant n'est configuré.
# Activé par défaut seulement si aucune session (refresh token ou .session.json) n'est configurée.
_HAS_SESSION = bool(os.getenv("WIKIMASTERS_REFRESH_TOKEN")) or os.path.exists(
    os.getenv("WIKIMASTERS_SESSION_FILE", os.path.join(os.path.dirname(__file__), ".session.json")))
DEMO_MODE = _bool("WIKIMASTERS_DEMO", not (_HAS_SESSION or (WIKIMASTERS_EMAIL and WIKIMASTERS_PASSWORD)))

# API WikiMasters (non officielle)
API_BASE_URL = os.getenv("WIKIMASTERS_API_URL", "https://www.wiki-masters.com/api").rstrip("/")
ENDPOINTS = {
    "login": os.getenv("WIKIMASTERS_EP_LOGIN", "/auth/login"),
    "owned_cards": os.getenv("WIKIMASTERS_EP_OWNED", "/my-collection"),
    # Laisser vide tant que l'endpoint des prix/ventes n'est pas connu
    "sales_history": os.getenv("WIKIMASTERS_EP_SALES", "/marketplace/cards/{card_id}/sales"),
    # Enchères du marché : source des prix
    "market": os.getenv("WIKIMASTERS_EP_MARKET", "/marketplace"),
    # Catalogue complet du jeu ("Toutes les cartes")
    "catalog": os.getenv("WIKIMASTERS_EP_CATALOG", "/cards"),
}
# Nom du paramètre de recherche du catalogue (à vérifier : F12 sur "Toutes les cartes" en cherchant un nom)
# Recherche d'une carte précise sur le Marché (à vérifier : F12 sur le Marché en cherchant un nom)
MARKET_SEARCH_PARAM = os.getenv("WIKIMASTERS_MARKET_SEARCH_PARAM", "q")
MARKET_SEARCH_MAX_PAGES = int(os.getenv("WIKIMASTERS_MARKET_SEARCH_MAX_PAGES", "2"))
CATALOG_SEARCH_PARAM = os.getenv("WIKIMASTERS_CATALOG_SEARCH_PARAM", "q")
CATALOG_MAX_PAGES = int(os.getenv("WIKIMASTERS_CATALOG_MAX_PAGES", "5"))     # 50 cartes par page
CATALOG_CACHE_HOURS = float(os.getenv("WIKIMASTERS_CATALOG_CACHE_HOURS", "24"))
SALES_PARAMS = {"scope": os.getenv("WIKIMASTERS_SALES_SCOPE", "summary")}
# Cache disque des prix
PRICE_CACHE_FILE = os.getenv("WIKIMASTERS_PRICE_CACHE", os.path.join(os.path.dirname(__file__), ".price_cache.json"))
SALES_CACHE_HOURS = float(os.getenv("WIKIMASTERS_SALES_CACHE_HOURS", "12"))
MARKET_CACHE_MINUTES = float(os.getenv("WIKIMASTERS_MARKET_CACHE_MINUTES", "30"))
# Renouveler le jeton d'accès Supabase (valide 1 h) quand il reste moins de N secondes
TOKEN_REFRESH_MARGIN = int(os.getenv("WIKIMASTERS_TOKEN_REFRESH_MARGIN", "300"))
# Requêtes de ventes : parallélisme réduit, et pause quand le site freine (403/429)
SALES_WORKERS = int(os.getenv("WIKIMASTERS_SALES_WORKERS", "2"))
SALES_MAX_FORBIDDEN = int(os.getenv("WIKIMASTERS_SALES_MAX_FORBIDDEN", "3"))
SALES_PAUSE_SECONDS = int(os.getenv("WIKIMASTERS_SALES_PAUSE_SECONDS", "20"))
SALES_MAX_PAUSES = int(os.getenv("WIKIMASTERS_SALES_MAX_PAUSES", "3"))
# Ne pas chercher le prix réel des cartes mises en favoris (gain de temps)
SKIP_STARRED_PRICES = _bool("WIKIMASTERS_SKIP_STARRED", True)
# Mise à jour progressive de l'affichage tous les N prix récupérés
PRICE_BATCH = int(os.getenv("WIKIMASTERS_PRICE_BATCH", "50"))
# Une enchère sans offre plus de N fois au-dessus des ventes de cartes similaires est ignorée
MAX_ASK_RATIO = float(os.getenv("WIKIMASTERS_MAX_ASK_RATIO", "3"))
# Nombre minimum de cartes vendues comparables pour une estimation
MIN_SALES_SAMPLES = int(os.getenv("WIKIMASTERS_MIN_SALES_SAMPLES", "3"))
MARKET_PAGE_SIZE = int(os.getenv("WIKIMASTERS_MARKET_PAGE_SIZE", "50"))
MARKET_MAX_PAGES = int(os.getenv("WIKIMASTERS_MARKET_MAX_PAGES", "40"))
MARKET_SORT = os.getenv("WIKIMASTERS_MARKET_SORT", "recent")
# Paramètres fixes de /api/my-collection (?sort=rarity&page=0&stats=0)
OWNED_EXTRA_PARAMS = {"sort": "rarity", "stats": 0}
PAGE_START = int(os.getenv("WIKIMASTERS_PAGE_START", "0"))
MAX_PAGES = int(os.getenv("WIKIMASTERS_MAX_PAGES", "1000"))
# Pagination de la liste des cartes : "page" (?page=&limit=) ou "offset" (?offset=&limit=, style Supabase)
PAGINATION = os.getenv("WIKIMASTERS_PAGINATION", "page")

# Connexion Supabase (le site utilise Supabase Auth). Si renseigné, remplace l'endpoint "login".
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
# La connexion email/mot de passe est protégée par un captcha : on réutilise la session
# du navigateur via son refresh token (cookie sb-<projet>-auth-token).
WIKIMASTERS_REFRESH_TOKEN = os.getenv("WIKIMASTERS_REFRESH_TOKEN", "")
# Le refresh token change à chaque renouvellement : le dernier est sauvegardé ici
SESSION_FILE = os.getenv("WIKIMASTERS_SESSION_FILE", os.path.join(os.path.dirname(__file__), ".session.json"))

PAGE_SIZE = int(os.getenv("WIKIMASTERS_PAGE_SIZE", "50"))
REQUEST_TIMEOUT = int(os.getenv("WIKIMASTERS_TIMEOUT", "20"))
# Pause entre deux requêtes pour respecter le rate limit
REQUEST_DELAY = float(os.getenv("WIKIMASTERS_REQUEST_DELAY", "0.2"))
MAX_WORKERS = int(os.getenv("WIKIMASTERS_MAX_WORKERS", "4"))

# Bibliothèques (ensembles de cartes à compléter) : données personnelles
LIBRARIES_FILE = os.getenv("WIKIMASTERS_LIBRARIES_FILE", os.path.join(os.path.dirname(__file__), "libraries.json"))

# Valeurs par défaut des paramètres de vente
DEFAULT_MIN_SELL_PRICE = float(os.getenv("DEFAULT_MIN_SELL_PRICE", "0"))
DEFAULT_MIN_BUYERS = int(os.getenv("DEFAULT_MIN_BUYERS", "0"))
BEST_SELLERS_LIMIT = 50
