"""Configure .env avec la session WikiMasters de ton navigateur.

Usage : python set_session.py
- crée .env depuis .env.example s'il n'existe pas ;
- demande la clé publique "apikey" du site si elle manque ;
- génère une SECRET_KEY si besoin ;
- enregistre le refresh token extrait du cookie sb-cyrxjeppjqsxxjayfrur-auth-token.0
  (F12 > Application > Cookies > https://www.wiki-masters.com).
"""
import base64
import json
import os
import re
import secrets
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(ROOT, ".env")
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")
SESSION_FILE = os.path.join(ROOT, ".session.json")


def extract_refresh_token(cookie_value):
    value = cookie_value.strip().strip('"')
    if value.startswith("base64-"):
        value = value[len("base64-"):]
        # base64url sans padding ; le cookie .0 peut être tronqué, on décode ce qu'on peut
        value = value.replace("-", "+").replace("_", "/")
        value = value[: len(value) - len(value) % 4]
        value = base64.b64decode(value).decode("utf-8", errors="ignore")
    match = re.search(r'"refresh_token"\s*:\s*"([^"]+)"', value)
    if match:
        return match.group(1)
    try:
        return json.loads(value)["refresh_token"]
    except (ValueError, KeyError, TypeError):
        return None


def get_var(env, name):
    match = re.search(rf"^{name}=(.*)$", env, flags=re.M)
    return match.group(1).strip() if match else ""


def set_var(env, name, value):
    line = f"{name}={value}"
    if re.search(rf"^{name}=.*$", env, flags=re.M):
        return re.sub(rf"^{name}=.*$", lambda _: line, env, flags=re.M)
    return env.rstrip("\n") + "\n" + line + "\n"


def main():
    if not os.path.exists(ENV_FILE):
        shutil.copy(ENV_EXAMPLE, ENV_FILE)
        print(".env créé à partir de .env.example")
    with open(ENV_FILE, encoding="utf-8") as f:
        env = f.read()

    if get_var(env, "SECRET_KEY") in ("", "change-moi", "change-me"):
        env = set_var(env, "SECRET_KEY", secrets.token_hex(24))

    if not get_var(env, "SUPABASE_ANON_KEY"):
        print("\nClé publique du site (une seule fois) : sur wiki-masters.com, F12 > Network,")
        print("clique une requête vers 'supabase.co' et copie la valeur de l'en-tête 'apikey' (commence par eyJ).")
        key = input("apikey : ").strip()
        if key:
            env = set_var(env, "SUPABASE_ANON_KEY", key)

    cookie = input("\nColle la valeur du cookie sb-...-auth-token.0 : ")
    token = extract_refresh_token(cookie)
    if not token:
        print("refresh_token introuvable : vérifie que tu as copié le cookie .0 en entier.")
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(env)
        return
    env = set_var(env, "WIKIMASTERS_REFRESH_TOKEN", token)
    env = set_var(env, "WIKIMASTERS_DEMO", "0")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(env)
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    print("Session enregistrée dans .env (ancienne .session.json supprimée). Lance : python app.py")


if __name__ == "__main__":
    main()
