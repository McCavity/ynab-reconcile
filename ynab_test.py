"""
YNAB API – Verbindungstest
Testet die Verbindung zur YNAB API und zeigt Budgets und Konten an.
"""

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

# ── Konfiguration ─────────────────────────────────────────────────────────────

def load_token() -> str:
    """Liest den API-Token aus der .env-Datei oder Umgebungsvariable."""
    # 1. Umgebungsvariable prüfen
    token = os.environ.get("YNAB_API_TOKEN", "")
    if token and token != "dein_token_hier_eintragen":
        return token

    # 2. .env-Datei im selben Verzeichnis suchen
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("YNAB_API_TOKEN=") and not line.startswith("#"):
                token = line.split("=", 1)[1].strip()
                if token and token != "dein_token_hier_eintragen":
                    return token

    print("❌ Kein API-Token gefunden!")
    print("   Bitte lege eine .env-Datei an (siehe .env.example) oder")
    print("   setze die Umgebungsvariable YNAB_API_TOKEN.")
    sys.exit(1)


# ── API-Hilfsfunktion ─────────────────────────────────────────────────────────

BASE_URL = "https://api.ynab.com/v1"

def api_get(path: str, token: str) -> dict:
    """Führt einen GET-Request gegen die YNAB API aus."""
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"❌ HTTP-Fehler {e.code}: {body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌ Verbindungsfehler: {e.reason}")
        sys.exit(1)


# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  YNAB API – Verbindungstest")
    print("=" * 60)

    token = load_token()
    print(f"✅ Token geladen (endet auf: ...{token[-6:]})\n")

    # 1. Benutzerinfo abrufen
    print("📋 Benutzerinformationen:")
    user_data = api_get("/user", token)
    user = user_data["data"]["user"]
    print(f"   ID: {user['id']}")
    print()

    # 2. Budgets abrufen
    print("💰 Verfügbare Budgets:")
    budgets_data = api_get("/budgets", token)
    budgets = budgets_data["data"]["budgets"]

    if not budgets:
        print("   Keine Budgets gefunden.")
        return

    for i, budget in enumerate(budgets, 1):
        print(f"\n   [{i}] {budget['name']}")
        print(f"       ID:       {budget['id']}")
        print(f"       Währung:  {budget.get('currency_format', {}).get('iso_code', 'unbekannt')}")
        print(f"       Geändert: {budget.get('last_modified_on', 'unbekannt')}")

    # 3. Konten des ersten Budgets abrufen
    first_budget = budgets[0]
    print(f"\n🏦 Konten in '{first_budget['name']}':")
    accounts_data = api_get(f"/budgets/{first_budget['id']}/accounts", token)
    accounts = accounts_data["data"]["accounts"]

    open_accounts = [a for a in accounts if not a.get("closed") and not a.get("deleted")]
    for account in open_accounts:
        balance = account["balance"] / 1000  # YNAB speichert in Milli-Einheiten
        cleared = account["cleared_balance"] / 1000
        uncleared = account["uncleared_balance"] / 1000
        print(f"\n   📂 {account['name']} ({account['type']})")
        print(f"      Saldo:         {balance:>10.2f}")
        print(f"      Abgeglichen:   {cleared:>10.2f}")
        print(f"      Nicht abgegl.: {uncleared:>10.2f}")

    print("\n" + "=" * 60)
    print("✅ Verbindungstest erfolgreich!")
    print("=" * 60)


if __name__ == "__main__":
    main()
