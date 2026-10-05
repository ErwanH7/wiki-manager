"""Diagnostic du prix d'une carte de ta collection.

Usage : venv\\Scripts\\python check_price.py "Tombeau hun"
Affiche la réponse brute de l'endpoint des ventes et le prix calculé par l'app.
"""
import json
import logging
import sys

import config
from wikimasters_api import WikiMastersClient, enrich_cards, normalize_text

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def main():
    if len(sys.argv) < 2:
        print('Usage : python check_price.py "nom de la carte"')
        return
    query = normalize_text(" ".join(sys.argv[1:]))

    client = WikiMastersClient(config.WIKIMASTERS_EMAIL, config.WIKIMASTERS_PASSWORD)
    client.login()
    matches = [c for c in client.get_all_owned_cards() if query in normalize_text(c["title"])]
    if not matches:
        print("Aucune carte de ta collection ne correspond.")
        return

    for card in matches:
        print("=" * 60)
        print(f"{card['title']} | rareté {card['rarity']} | shiny {card['is_shiny']} | id {card['id']}")
        url = client._url("sales_history", card_id=card["id"])
        response = client.session.get(url, params=config.SALES_PARAMS, timeout=config.REQUEST_TIMEOUT)
        print(f"Ventes : HTTP {response.status_code}")
        print(response.text[:500])
        try:
            print("Lu par l'app :", json.dumps(client.get_card_sales_history(card["id"]), ensure_ascii=False))
        except Exception as e:
            print("Erreur de lecture :", e)

    print("=" * 60)
    for card in enrich_cards(client, matches):
        print(f"Prix calculé pour {card['title']} : {card['avg_price']} (source {card['price_source']})"
              + (f" - {card['price_note']}" if card.get("price_note") else ""))


if __name__ == "__main__":
    main()
