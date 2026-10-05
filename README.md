# WikiMasters Collection Manager 🎴

Application web locale pour **analyser ta collection [WikiMasters](https://www.wiki-masters.com)** :
valeur de tes cartes d'après les ventes réelles du marché, cartes les plus rentables à vendre,
regroupement par thème, exports CSV/JSON.

> Projet perso non officiel, sans lien avec WikiMasters. Il utilise ta propre session et
> uniquement des données que le site t'affiche déjà. Respecte les conditions d'utilisation du site.

---

## Démarrage rapide

### 1. Prérequis
- [Python 3.10+](https://www.python.org/downloads/) (coche « Add Python to PATH » sous Windows)
- Un compte WikiMasters

### 2. Installation

```bash
git clone <url-de-ton-repo>
cd wikitcg
python -m venv venv
```

Active l'environnement puis installe les dépendances :

| Windows (PowerShell) | Linux / macOS |
|---|---|
| `venv\Scripts\activate` | `source venv/bin/activate` |

```bash
pip install -r requirements.txt
```

### 3. Essayer sans compte (mode démo)

```bash
python app.py
```

Ouvre <http://localhost:5000> : sans configuration, l'app tourne avec une collection fictive.

### 4. Connecter ton compte WikiMasters

La connexion par mot de passe du site est protégée par un captcha : l'app réutilise donc
**la session de ton navigateur**. Le script `set_session.py` fait toute la configuration.

1. Connecte-toi sur <https://www.wiki-masters.com>.
2. Ouvre les outils développeur (**F12**).
3. **Clé du site** (demandée une seule fois) : onglet **Network** (Réseau), recharge la page,
   clique une requête vers `supabase.co` et copie l'en-tête **`apikey`** (commence par `eyJ`).
4. **Session** : onglet **Application** › **Cookies** › `https://www.wiki-masters.com`,
   copie la valeur de **`sb-cyrxjeppjqsxxjayfrur-auth-token.0`** (commence par `base64-`).
5. Lance le script et colle ces valeurs quand il les demande :

   ```bash
   python set_session.py
   ```

   Il crée `.env`, y enregistre la clé et ta session, et génère une `SECRET_KEY`.
6. Lance l'app :

   ```bash
   python app.py
   ```

   puis ouvre <http://localhost:5000> et clique **« Se connecter »**.

> ⚠️ Ensuite, **ne clique pas sur « Déconnexion » sur wiki-masters.com** : cela révoque aussi la
> session de l'app. Si ça arrive (erreur « session expirée »), relance simplement `python set_session.py`.

L'app renouvelle seule sa session (le jeton d'accès expire toutes les heures) et la sauvegarde
dans `.session.json`.

---

## Fonctionnalités

| Onglet | Contenu |
|---|---|
| 📊 **Dashboard** | Nombre de cartes, valeur totale, répartition par rareté et par catégorie, carte la plus précieuse |
| 💰 **À Vendre** | Cartes classées par score de rentabilité, filtres rareté / prix min-max |
| 🏷️ **Catégories** | Regroupement thématique (manga, films, porno, seconde guerre mondiale, sport, lieux…) |
| 📋 **Collection** | Recherche, filtres, tri, pagination, ⭐ favoris, exports CSV (Excel) et JSON |
| ⚙️ **Paramètres** | Prix minimum / acheteurs minimum, état du cache, vider le cache des prix |

Dans la fenêtre **Détails** d'une carte : source du prix, historique des enchères, cartes de la
même série et bouton **🔍 Diagnostic du prix** (réponse brute du site).

### Chargement rapide, même avec beaucoup de cartes
- La collection s'affiche **immédiatement** avec les prix déjà connus.
- Les prix réels manquants sont récupérés **en arrière-plan** : un bandeau montre l'avancement et
  l'affichage se met à jour tout seul.
- Les prix sont mis en **cache** (`.price_cache.json`, 12 h) : les chargements suivants ne
  redemandent que les cartes nouvelles ou périmées.
- Les cartes en **favoris ⭐** ne sont pas recherchées (désactivable, voir Réglages).
- Si le site est en panne, l'app affiche la **dernière collection connue** avec un avertissement.

---

## Comment le prix est calculé

Du plus fiable au moins fiable :

| # | Source | Affichage |
|---|---|---|
| 1 | Prix de vente moyen réel de la carte **dans sa rareté** | `42` |
| 2 | Même prix réel, gardé en cache si le site ne répond pas | `42` + note |
| 3 | Ventes réelles de la carte **dans une autre rareté** (si cohérent avec les cartes similaires) | `42*` |
| 4 | Enchère en cours la **plus basse** de la carte | `42` |
| 5 | Estimation d'après les **ventes réelles de cartes du même type**, sinon de même rareté | `≈ 42` |
| 6 | Estimation d'après les prix demandés les plus bas de cartes du même type (peu fiable) | `≈ 42` |

Garde-fous : une enchère **sans offre** ou une vente d'une **autre rareté** plus de 3× au-dessus des
ventes de cartes similaires est ignorée (ex. une carte à 25 000 alors que ses semblables se
vendent 5). Survole un prix « ≈ » ou ouvre **Détails** pour voir l'explication.

**Score « À Vendre »** :

```
score = prix × max(demande, 1) × multiplicateur de rareté / 100
C 1x · PC 2x · R 3x · SR 4x · UR 5x · L 6x
```

La **demande** = enchères en cours de la carte ayant reçu une offre. Pour un compte non Pro, le
site ne donne que la moyenne des ventes, pas leur nombre (affiché « – »).

---

## Réglages (`.env`)

Toutes les options sont documentées dans [.env.example](.env.example). Les plus utiles :

| Variable | Défaut | Rôle |
|---|---|---|
| `WIKIMASTERS_SALES_WORKERS` | `2` | Requêtes de prix en parallèle. Mets `1` si tu vois des erreurs 403/429 |
| `WIKIMASTERS_REQUEST_DELAY` | `0.3` | Pause entre deux requêtes (secondes) |
| `WIKIMASTERS_SALES_CACHE_HOURS` | `12` | Durée de validité d'un prix en cache |
| `WIKIMASTERS_SKIP_STARRED` | `1` | `0` pour aussi chercher le prix des favoris |
| `WIKIMASTERS_MAX_ASK_RATIO` | `3` | Seuil des garde-fous de prix |
| `WIKIMASTERS_DEMO` | auto | `1` pour forcer le mode démo |

Catégories : ajoute tes mots-clés dans `THEMATIC_GROUPS` de [wikimasters_api.py](wikimasters_api.py)
(les catégories de `PRIORITY_GROUPS` l'emportent sur les autres).

---

## Dépannage

| Problème | Solution |
|---|---|
| `captcha protection` / `session expirée` / `Invalid Refresh Token` | Relance `python set_session.py` avec un cookie récent |
| Erreurs **403** dans le terminal | Le site freine les requêtes : `WIKIMASTERS_SALES_WORKERS=1` et `WIKIMASTERS_REQUEST_DELAY=1` |
| Erreur **500** du site | Panne côté WikiMasters : l'app réessaie puis affiche la dernière collection connue |
| Un prix semble faux | **Détails › 🔍 Diagnostic du prix**, et vérifie la « Source du prix » |
| Prix trop anciens | **Paramètres › Vider le cache des prix et recharger** |
| Page qui ne se met pas à jour | `Ctrl + F5` |

`python check_price.py "nom de carte"` affiche aussi le diagnostic d'une carte en ligne de
commande (à lancer **app fermée** : il utilise sa propre session).

---

## Structure

```
app.py               Serveur Flask : routes /api/*, chargement en arrière-plan
wikimasters_api.py   Client WikiMasters (Supabase), marché, calcul des prix, catégories
price_cache.py       Cache disque des prix et de la dernière collection
config.py            Lecture de .env
set_session.py       Configuration de la session navigateur
check_price.py       Diagnostic d'une carte en ligne de commande
templates/, static/  Interface web
```

Détails techniques : [DEVELOPMENT.md](DEVELOPMENT.md).

## Fichiers à ne jamais publier

`.env`, `.session.json` et `.price_cache.json` contiennent ta session et tes données : ils sont
déjà dans [.gitignore](.gitignore). Avant un `git push`, vérifie avec `git status` qu'ils n'apparaissent pas.

## Licence

Usage personnel. Respecte les conditions d'utilisation de WikiMasters.
