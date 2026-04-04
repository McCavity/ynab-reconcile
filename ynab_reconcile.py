#!/usr/bin/env python3
"""
YNAB Abgleich-Tool
Interaktiver Abgleich von YNAB-Transaktionen mit Kontoauszugsdaten (z.B. aus Finanzblick)
"""

import os
import re
import sys
import csv
import json
import difflib
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional

BASE_URL = "https://api.ynab.com/v1"

# ── Farben ────────────────────────────────────────────────────────────────────
G  = "\033[32m"   # grün
Y  = "\033[33m"   # gelb
R  = "\033[31m"   # rot
C  = "\033[36m"   # cyan
W  = "\033[1m"    # fett
DIM= "\033[2m"    # gedimmt
X  = "\033[0m"    # reset

def ok(s):   return f"{G}{s}{X}"
def warn(s): return f"{Y}{s}{X}"
def err(s):  return f"{R}{s}{X}"
def bold(s): return f"{W}{s}{X}"
def info(s): return f"{C}{s}{X}"
def dim(s):  return f"{DIM}{s}{X}"

# ── Token laden ───────────────────────────────────────────────────────────────
def load_token() -> str:
    token = os.environ.get("YNAB_API_TOKEN", "")
    if token and token != "dein_token_hier_eintragen":
        return token
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("YNAB_API_TOKEN=") and not line.startswith("#"):
                token = line.split("=", 1)[1].strip()
                if token and token != "dein_token_hier_eintragen":
                    return token
    print(err("❌ Kein API-Token gefunden! Bitte .env-Datei anlegen."))
    sys.exit(1)

# ── API-Hilfsfunktionen ───────────────────────────────────────────────────────
def api_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(err(f"❌ HTTP {e.code}: {e.read().decode()}"))
        sys.exit(1)
    except urllib.error.URLError as e:
        print(err(f"❌ Verbindungsfehler: {e.reason}"))
        sys.exit(1)

def api_patch(path: str, data: dict, token: str) -> dict:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, method="PATCH",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(err(f"❌ HTTP {e.code}: {e.read().decode()}"))
        return {}

def api_post(path: str, data: dict, token: str) -> dict:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(err(f"❌ HTTP {e.code}: {e.read().decode()}"))
        return {}

# ── Betrags-Parsing ───────────────────────────────────────────────────────────
def parse_amount(s: str) -> Optional[float]:
    """Parst deutschen oder englischen Betragsstring zu float."""
    s = s.strip().replace("€", "").replace("EUR", "").replace("+", "").strip()
    # Negativ-Zeichen erhalten
    negative = s.startswith("-")
    s = s.lstrip("-").strip()
    # Tausender- und Dezimaltrenner erkennen
    if "," in s and "." in s:
        # Welches kommt zuletzt?
        if s.rindex(",") > s.rindex("."):
            # Deutsch: 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:
            # Englisch: 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        # Nur Komma → Dezimaltrenner (deutsch)
        s = s.replace(",", ".")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None

def fmt_eur(amount_float: float, colored: bool = True) -> str:
    s = f"{amount_float:+.2f} €"
    if not colored:
        return s
    return err(s) if amount_float < 0 else ok(s)

# ── Payee-Alias-System ────────────────────────────────────────────────────────
# Speichert Zuordnungen: Bank-Payee (lowercase) → YNAB-Payee-Name
# Datei: aliases.json im selben Ordner wie das Skript

ALIASES_FILE = Path(__file__).parent / "aliases.json"

def load_aliases() -> dict:
    """Lädt gespeicherte Payee-Aliases aus aliases.json."""
    if ALIASES_FILE.exists():
        try:
            return json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_alias(bank_payee: str, ynab_payee: str) -> None:
    """Speichert einen neuen Alias dauerhaft."""
    aliases = load_aliases()
    aliases[bank_payee.lower().strip()] = ynab_payee.strip()
    ALIASES_FILE.write_text(
        json.dumps(aliases, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(ok(f"  💾 Alias gespeichert: '{bank_payee}' → '{ynab_payee}'"))

def resolve_alias(bank_payee: str, aliases: dict) -> str:
    """
    Gibt den YNAB-Payee-Namen für einen Bank-Payee zurück (wenn Alias vorhanden),
    sonst den Original-Bank-Payee-Namen.
    Sucht zuerst exakt, dann nach ob der Bank-Payee einen bekannten Alias enthält.
    """
    key = bank_payee.lower().strip()
    if key in aliases:
        return aliases[key]
    # Teilstring-Suche: z.B. "Takeaway.com GmbH" matcht Alias "takeaway.com"
    for alias_key, ynab_name in aliases.items():
        if alias_key in key or key in alias_key:
            return ynab_name
    return bank_payee

# ── Konfiguration (Dauerbucher / deferred payees) ────────────────────────────
# Speichert Payees, deren YNAB-Einträge automatisch übersprungen werden,
# weil sie monatlich aufsummiert abgerechnet werden (z.B. RMV).
# Datei: config.json im selben Ordner wie das Skript

CONFIG_FILE = Path(__file__).parent / "config.json"

def load_config() -> dict:
    """Lädt die Konfigurationsdatei (oder gibt Standardwerte zurück)."""
    defaults = {"deferred_payees": []}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception:
            pass
    return defaults

def save_config(config: dict) -> None:
    """Speichert die Konfiguration dauerhaft."""
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def is_deferred_payee(payee_name: str, config: dict) -> bool:
    """Prüft ob ein Payee in der Dauerbucher-Liste steht (Teilstring-Suche)."""
    if not payee_name:
        return False
    lower = payee_name.lower()
    return any(kw in lower for kw in config.get("deferred_payees", []))

def add_deferred_payee(payee_name: str, config: dict) -> None:
    """Fügt einen Payee zur Dauerbucher-Liste hinzu und speichert."""
    # Kürzestes sinnvolles Schlüsselwort: erstes Wort (mindestens 4 Zeichen)
    words = payee_name.lower().split()
    keyword = next((w for w in words if len(w) >= 4), payee_name.lower()[:10])
    if keyword not in config["deferred_payees"]:
        config["deferred_payees"].append(keyword)
        save_config(config)
        print(ok(f"  💾 Dauerbucher gespeichert: '{keyword}' (aus '{payee_name}')"))


# ── YNAB-Stammdaten laden ────────────────────────────────────────────────────
def load_ynab_payees(budget_id: str, token: str) -> list:
    """Lädt alle Payees aus YNAB (ohne gelöschte)."""
    data = api_get(f"/budgets/{budget_id}/payees", token)
    return [p for p in data["data"]["payees"] if not p.get("deleted")]

def load_ynab_categories(budget_id: str, token: str) -> list:
    """
    Lädt alle Kategorien aus YNAB als flache Liste mit Gruppenname.
    Gibt eine Liste von Dicts: {id, name, group_name} zurück.
    """
    data = api_get(f"/budgets/{budget_id}/categories", token)
    result = []
    for group in data["data"]["category_groups"]:
        if group.get("deleted") or group.get("hidden"):
            continue
        group_name = group["name"]
        for cat in group.get("categories", []):
            if cat.get("deleted") or cat.get("hidden"):
                continue
            result.append({
                "id":         cat["id"],
                "name":       cat["name"],
                "group_name": group_name,
                "display":    f"{group_name} › {cat['name']}"
            })
    return result

def fuzzy_pick_payee(query: str, payees: list) -> str:
    """
    Sucht fuzzy in der YNAB-Payee-Liste und lässt den User auswählen.
    Gibt den gewählten Payee-Namen zurück (oder den eingetippten Query wenn kein Match).
    """
    if not query or not payees:
        return query
    lower = query.lower()
    # Zuerst: Payees die den Query enthalten
    matches = [p for p in payees if lower in p["name"].lower()]
    # Falls zu viele: nach Ähnlichkeit sortieren
    if not matches:
        scored = sorted(
            payees,
            key=lambda p: difflib.SequenceMatcher(None, lower, p["name"].lower()).ratio(),
            reverse=True
        )
        matches = scored[:5]
    else:
        matches = matches[:8]

    if not matches:
        return query

    if len(matches) == 1 and matches[0]["name"].lower() == lower:
        return matches[0]["name"]  # exakter Treffer, direkt zurückgeben

    print(f"  {dim('Gefundene Payees:')}")
    for i, p in enumerate(matches, 1):
        print(f"    [{i}] {p['name']}")
    print(f"    [0] '{query}' als neuen Payee anlegen")

    choice = input("  → ").strip()
    try:
        idx = int(choice)
        if idx == 0:
            return query
        return matches[idx - 1]["name"]
    except (ValueError, IndexError):
        return query

def pick_category(categories: list) -> Optional[str]:
    """
    Lässt den User eine Kategorie aus der YNAB-Liste wählen.
    Gibt die Kategorie-ID zurück oder None wenn keine gewählt.
    """
    if not categories:
        return None

    query = input(f"  Kategorie suchen {dim('(leer = keine Kategorie)')}: ").strip()
    if not query:
        return None

    lower = query.lower()
    matches = [c for c in categories if lower in c["display"].lower()]
    if not matches:
        # Fuzzy fallback
        matches = sorted(
            categories,
            key=lambda c: difflib.SequenceMatcher(None, lower, c["display"].lower()).ratio(),
            reverse=True
        )[:5]

    matches = matches[:6]
    if not matches:
        print(warn("  Keine Kategorie gefunden."))
        return None

    print(f"  {dim('Gefundene Kategorien:')}")
    for i, c in enumerate(matches, 1):
        print(f"    [{i}] {c['display']}")
    print(f"    [0] Keine Kategorie")

    choice = input("  → ").strip()
    try:
        idx = int(choice)
        if idx == 0:
            return None
        return matches[idx - 1]["id"]
    except (ValueError, IndexError):
        return None


# ── Dialog: neue Transaktion in YNAB anlegen ──────────────────────────────────
def create_new_transaction_dialog(bt: dict, account_id: str, aliases: dict,
                                  payees: list, categories: list) -> Optional[dict]:
    """
    Interaktiver Dialog zum Anlegen einer neuen YNAB-Transaktion.
    Enthält Payee-Suche, Kategorie-Auswahl, Datum und Betrag.
    Am Ende: Zusammenfassung mit [s]peichern / [w]iederholen / [a]bbrechen.
    Gibt ein fertiges Transaktions-Dict zurück oder None wenn abgebrochen.
    """
    while True:
        print()

        # ── Datum ──────────────────────────────────────────────────────────────
        default_date = bt["date"].strftime("%d.%m.%Y")
        raw_date = input(f"  Datum [{default_date}] (Enter = übernehmen): ").strip()
        if raw_date:
            parsed = None
            for fmt in ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"]:
                try:
                    parsed = datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    continue
            if not parsed:
                print(warn(f"  ⚠️  Ungültiges Datum, verwende {default_date}"))
                parsed = bt["date"]
            tx_date = parsed
        else:
            tx_date = bt["date"]

        # ── Payee ───────────────────────────────────────────────────────────────
        default_payee = resolve_alias(bt["payee"], aliases)
        print(f"  Payee suchen/eingeben [{dim(default_payee)}]:")
        raw_payee = input("  → ").strip()
        if not raw_payee:
            payee_name = default_payee
        else:
            payee_name = fuzzy_pick_payee(raw_payee, payees)

        # ── Kategorie ───────────────────────────────────────────────────────────
        category_id = pick_category(categories)

        # ── Betrag ──────────────────────────────────────────────────────────────
        raw_amt = input(f"  Betrag [{bt['amount']:+.2f} €] (Enter = übernehmen): ").strip()
        amount = parse_amount(raw_amt) if raw_amt else bt["amount"]
        if amount is None:
            print(warn("  ⚠️  Ungültiger Betrag, verwende Bank-Betrag."))
            amount = bt["amount"]

        # ── Memo ────────────────────────────────────────────────────────────────
        memo = input("  Memo (optional): ").strip()

        # ── Zusammenfassung & Bestätigung ───────────────────────────────────────
        cat_display = next((c["display"] for c in categories if c["id"] == category_id), "—")
        print(f"\n  {bold('Neue Transaktion – Zusammenfassung:')}")
        print(f"    Datum:      {tx_date.strftime('%d.%m.%Y')}")
        print(f"    Payee:      {payee_name}")
        print(f"    Kategorie:  {cat_display}")
        print(f"    Betrag:     {fmt_eur(amount)}")
        if memo:
            print(f"    Memo:       {memo}")

        print(f"\n  {bold('[s]')}peichern  {bold('[w]')}iederholen  {bold('[a]')}bbrechen")
        confirm = input("  → ").strip().lower()

        if confirm == "s" or confirm == "":
            tx = {
                "account_id":  account_id,
                "date":        tx_date.strftime("%Y-%m-%d"),
                "amount":      int(round(amount * 1000)),
                "payee_name":  payee_name,
                "memo":        memo,
                "cleared":     "cleared"
            }
            if category_id:
                tx["category_id"] = category_id
            # Alias anbieten wenn Payee vom Bank-Namen abweicht
            if payee_name != bt["payee"]:
                _offer_alias(bt["payee"], payee_name, aliases)
            return tx

        elif confirm == "w":
            print(info("  🔄 Eingabe wiederholen..."))
            continue  # Dialog neu starten

        else:  # "a" oder alles andere
            return None


# ── Finanzblick CSV-Parser ────────────────────────────────────────────────────
# Spalten: Buchungsdatum;Wertstellungsdatum;Empfaenger;Verwendungszweck;
#          Buchungstext;Betrag;IBAN;BIC;Kategorie;Konto;Umbuchung;Notiz;
#          Schlagworte;SteuerKategorie;ParentKategorie;AbweichenderEmpfaenger;
#          Splitbuchung;Auswertungsdatum

def _extract_payee_from_csv_row(empfaenger: str, verwendungszweck: str, abweichend: str) -> str:
    """
    Ermittelt den besten Payee-Namen aus einer Finanzblick-CSV-Zeile.
    Priorität:
      1. Für PayPal: echten Händler aus dem Verwendungszweck extrahieren
      2. AbweichenderEmpfaenger (wenn vorhanden)
      3. Empfaenger
    """
    # PayPal: echter Händler steckt im Verwendungszweck
    if "paypal" in empfaenger.lower():
        # Muster: "PP.1234.PP/. Händlername, Ihr Einkauf bei ..."
        m = re.search(r'/\.\s+(.+?),\s*(?:Ihr Einkauf|EREF|MREF)', verwendungszweck)
        if m:
            return m.group(1).strip()

    if abweichend.strip():
        return abweichend.strip()

    return empfaenger.strip()


def parse_finanzblick_csv(filepath: str) -> list:
    """Liest eine Buchungsliste-CSV aus Finanzblick und gibt eine Liste von Transaktions-Dicts zurück."""
    transactions = []
    skipped = []

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            # Datum
            date_str = row.get("Buchungsdatum", "").strip()
            parsed_date = None
            for fmt in ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"]:
                try:
                    parsed_date = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
            if not parsed_date:
                skipped.append(date_str)
                continue

            # Betrag
            amount = parse_amount(row.get("Betrag", ""))
            if amount is None:
                skipped.append(row.get("Betrag", "?"))
                continue

            payee = _extract_payee_from_csv_row(
                empfaenger      = row.get("Empfaenger", ""),
                verwendungszweck= row.get("Verwendungszweck", ""),
                abweichend      = row.get("AbweichenderEmpfaenger", "")
            )

            transactions.append({
                "date":   parsed_date,
                "amount": amount,
                "payee":  payee,
                "memo":   row.get("Verwendungszweck", "").strip()[:100],
                "raw":    f"{date_str} {amount:+.2f} {payee}"
            })

    if skipped:
        print(warn(f"⚠️  {len(skipped)} Zeile(n) übersprungen (nicht parsbar)."))

    return transactions


def find_csv_files(folder: Path) -> list:
    """Findet alle CSV-Dateien im angegebenen Ordner."""
    return sorted(folder.glob("*.csv"))


# ── Bank-Transaktionen einlesen ───────────────────────────────────────────────
def read_bank_transactions() -> list:
    print(f"""
{bold('Bank-Transaktionen einfügen:')}
Kopiere die Transaktionen aus Finanzblick (oder einer anderen Quelle)
und füge sie hier ein. Jede Zeile = eine Transaktion.

{bold('Unterstützte Formate (Tab- oder Semikolon-getrennt):')}
  {dim('Datum        Betrag      Beschreibung')}
  {info('01.04.2026   -45,90      REWE Saarbrücken')}
  {info('02.04.2026   +1.200,00   Gehalt GmbH & Co')}
  {info('03.04.2026   -12,99      Netflix')}

Leere Zeile eingeben zum Abschließen:
""")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            if lines:
                break
        else:
            lines.append(line)

    transactions = []
    skipped = []

    for line in lines:
        # Trennzeichen ermitteln
        parts = None
        for sep in ["\t", ";", "|"]:
            if sep in line:
                parts = [p.strip() for p in line.split(sep, 2)]
                break
        if parts is None:
            # Leerzeichen: erstes Token = Datum, zweites = Betrag, Rest = Beschreibung
            parts = line.split(None, 2)

        if len(parts) < 2:
            skipped.append(line)
            continue

        # Datum parsen
        date_str = parts[0].strip()
        parsed_date = None
        for fmt in ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"]:
            try:
                parsed_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue

        if not parsed_date:
            skipped.append(line)
            continue

        # Betrag parsen
        amount = parse_amount(parts[1])
        if amount is None:
            skipped.append(line)
            continue

        payee = parts[2].strip() if len(parts) > 2 else ""

        transactions.append({
            "date": parsed_date,
            "amount": amount,
            "payee": payee,
            "raw": line.strip()
        })

    if skipped:
        print(warn(f"\n⚠️  {len(skipped)} Zeile(n) konnten nicht geparst werden:"))
        for s in skipped:
            print(dim(f"   {s}"))

    return transactions

# ── Matching ──────────────────────────────────────────────────────────────────
def match_transactions(ynab_txs: list, bank_txs: list, aliases: dict = None, config: dict = None) -> list:
    """
    Versucht, YNAB- und Bank-Transaktionen zu paaren.
    Rückgabe: Liste von Match-Dicts mit type='matched'|'ynab_only'|'ynab_deferred'|'bank_only'
    - ynab_deferred: YNAB-Einträge von Dauerbuchern (werden automatisch übersprungen)
    Aliases werden beim Payee-Score berücksichtigt.
    """
    if aliases is None:
        aliases = {}
    if config is None:
        config = {"deferred_payees": []}
    used_bank = set()
    matched_ynab = {}  # ynab_idx → (bank_idx, score)

    for yi, yt in enumerate(ynab_txs):
        ynab_amount = yt["amount"] / 1000.0
        ynab_date   = datetime.strptime(yt["date"], "%Y-%m-%d").date()
        ynab_payee  = (yt.get("payee_name") or "").lower()

        best_score = -1
        best_bi    = None

        for bi, bt in enumerate(bank_txs):
            if bi in used_bank:
                continue

            bank_amount = bt["amount"]
            bank_date   = bt["date"]
            # Alias anwenden: bekannter Bank-Payee → YNAB-Payee-Name
            resolved    = resolve_alias(bt["payee"], aliases)
            bank_payee  = resolved.lower()

            # --- Betrags-Score (wichtigster Faktor) ---
            amount_diff = abs(ynab_amount - bank_amount)
            rel_diff    = amount_diff / max(abs(ynab_amount), 0.01)
            if amount_diff < 0.01:
                amount_score = 1.0
            elif amount_diff < 0.05:
                amount_score = 0.95    # Rundungsdifferenz
            elif rel_diff < 0.05:
                amount_score = 0.75    # bis 5% Abweichung (z.B. Pfand)
            elif rel_diff < 0.15:
                amount_score = 0.40    # bis 15%
            else:
                continue               # zu weit weg

            # --- Datum-Score ---
            days_diff = abs((ynab_date - bank_date).days)
            if days_diff == 0:
                date_score = 1.0
            elif days_diff <= 3:
                date_score = 0.85
            elif days_diff <= 14:
                date_score = 0.55
            elif days_diff <= 30:
                date_score = 0.25
            else:
                date_score = 0.0

            # --- Payee-Score ---
            payee_score = difflib.SequenceMatcher(
                None, ynab_payee, bank_payee
            ).ratio()

            # Gesamt: Betrag am wichtigsten, dann Datum, dann Payee
            total = amount_score * 0.60 + date_score * 0.25 + payee_score * 0.15

            if total > best_score:
                best_score = total
                best_bi    = bi

        if best_bi is not None and best_score > 0.3:
            # Konflikt: Bank-Transaktion bereits besser vergeben?
            if best_bi in {v[0] for v in matched_ynab.values()}:
                existing_yi = next(k for k, v in matched_ynab.items() if v[0] == best_bi)
                if best_score > matched_ynab[existing_yi][1]:
                    # Neue YNAB-Transaktion ist besserer Match → alte verdrängen
                    del matched_ynab[existing_yi]
                    matched_ynab[yi] = (best_bi, best_score)
                # else: bestehender Match bleibt
            else:
                matched_ynab[yi] = (best_bi, best_score)

    # Ergebnisse aufbauen
    used_bank_final = set()
    results = []

    for yi, yt in enumerate(ynab_txs):
        if yi in matched_ynab:
            bi, score = matched_ynab[yi]
            used_bank_final.add(bi)
            bt = bank_txs[bi]
            results.append({
                "type":        "matched",
                "ynab":        yt,
                "bank":        bt,
                "score":       score,
                "amount_diff": abs(yt["amount"] / 1000.0 - bt["amount"])
            })
        else:
            payee_name = yt.get("payee_name") or ""
            tx_type = "ynab_deferred" if is_deferred_payee(payee_name, config) else "ynab_only"
            results.append({"type": tx_type, "ynab": yt, "bank": None, "score": 0})

    for bi, bt in enumerate(bank_txs):
        if bi not in used_bank_final:
            results.append({"type": "bank_only", "ynab": None, "bank": bt, "score": 0})

    # Sortierung: gute Matches zuerst, dann YNAB-only, dann Bank-only
    def sort_key(r):
        if r["type"] == "matched":   return (0, -r["score"])
        if r["type"] == "ynab_only": return (1, 0)
        return (2, 0)

    results.sort(key=sort_key)
    return results

# ── Anzeige einer Transaktion ─────────────────────────────────────────────────
def show_match(match: dict, idx: int, total: int):
    print(f"\n{'─' * 62}")
    print(bold(f" Transaktion {idx} von {total}"))

    if match["type"] == "matched":
        score = match["score"]
        if score >= 0.85:
            conf = ok("✅ Gute Übereinstimmung")
        elif score >= 0.60:
            conf = warn("⚠️  Unsichere Übereinstimmung")
        else:
            conf = err("❓ Schwache Übereinstimmung")

        yt = match["ynab"]
        bt = match["bank"]
        print(f" {conf}  {dim(f'({score*100:.0f}%)')}")
        print()
        print(f"  {bold('YNAB:')}  {yt['date']}  {fmt_eur(yt['amount']/1000):>18}  {yt.get('payee_name') or '—'}")
        print(f"  {bold('Bank:')}  {bt['date']}  {fmt_eur(bt['amount']):>18}  {bt['payee']}")
        if yt.get("memo"):
            print(f"  {dim('Memo:')}  {dim(yt['memo'])}")
        if match["amount_diff"] > 0.01:
            print(warn(f"\n  ⚠️  Betrag weicht um {match['amount_diff']:.2f} € ab"))

    elif match["type"] == "ynab_only":
        yt = match["ynab"]
        print(warn(" 📋 Nur in YNAB – noch nicht im Konto gebucht?"))
        print()
        print(f"  {bold('YNAB:')}  {yt['date']}  {fmt_eur(yt['amount']/1000):>18}  {yt.get('payee_name') or '—'}")
        if yt.get("memo"):
            print(f"  {dim('Memo:')}  {dim(yt['memo'])}")

    elif match["type"] == "ynab_deferred":
        yt = match["ynab"]
        print(dim(" 🔄 Dauerbucher – wird automatisch übersprungen"))
        print()
        print(f"  {bold('YNAB:')}  {yt['date']}  {fmt_eur(yt['amount']/1000):>18}  {dim(yt.get('payee_name') or '—')}")

    elif match["type"] == "bank_only":
        bt = match["bank"]
        print(err(" 🏦 Nur im Konto – fehlt in YNAB!"))
        print()
        print(f"  {bold('Bank:')}  {bt['date']}  {fmt_eur(bt['amount']):>18}  {bt['payee']}")

# ── Interaktiver Abgleich ─────────────────────────────────────────────────────
def _offer_alias(bank_payee: str, ynab_payee: str, aliases: dict) -> None:
    """Fragt ob ein Payee-Alias gespeichert werden soll (nur wenn Payees wirklich abweichen)."""
    if not bank_payee or not ynab_payee:
        return
    if bank_payee.lower().strip() == ynab_payee.lower().strip():
        return
    # Nicht anbieten wenn schon ein Alias existiert
    if resolve_alias(bank_payee, aliases) != bank_payee:
        return
    similarity = difflib.SequenceMatcher(None, bank_payee.lower(), ynab_payee.lower()).ratio()
    # Nur anbieten wenn Payees wirklich verschieden (< 80% Ähnlichkeit)
    if similarity >= 0.80:
        return
    prompt = dim(f'Alias merken?  Bank: "{bank_payee}" → YNAB: "{ynab_payee}"  [j/N]:')
    ans = input(f"  {prompt} ").strip().lower()
    if ans == "j":
        save_alias(bank_payee, ynab_payee)
        aliases[bank_payee.lower().strip()] = ynab_payee  # auch in-memory aktualisieren


def interactive_reconcile(matches: list, budget_id: str, account_id: str, token: str,
                          aliases: dict = None, config: dict = None,
                          payees: list = None, categories: list = None):
    if aliases is None:
        aliases = {}
    if config is None:
        config = {"deferred_payees": []}
    if payees is None:
        payees = []
    if categories is None:
        categories = []
    to_clear   = []   # [transaction_id, ...]
    to_update  = []   # [{"id":..., "amount":..., "cleared":"cleared"}, ...]
    to_create  = []   # [full transaction dict, ...]
    skipped    = 0
    deferred   = 0    # automatisch übersprungene Dauerbucher

    # Dauerbucher vorab zählen und aus interaktivem Loop herausfiltern
    active_matches  = [m for m in matches if m["type"] != "ynab_deferred"]
    deferred_matches = [m for m in matches if m["type"] == "ynab_deferred"]
    deferred = len(deferred_matches)
    if deferred:
        print(info(f"\n⏭️  {deferred} Dauerbucher-Einträge automatisch übersprungen "
                   f"({', '.join(set(m['ynab'].get('payee_name','?') for m in deferred_matches[:3]))}{'…' if deferred > 3 else ''})"))

    total = len(active_matches)

    for idx, match in enumerate(active_matches, 1):
        show_match(match, idx, total)

        # ── Gematchte Transaktion ──────────────────────────────────────────────
        if match["type"] == "matched":
            yt = match["ynab"]
            bt = match["bank"]
            has_diff = match["amount_diff"] > 0.01
            high_conf = match["score"] >= 0.85 and not has_diff

            if high_conf:
                print(f"\n  {bold('[c]')}lear  {bold('[e]')}dit Betrag  {bold('[s]')}kip  {dim('(Enter = clear)')}")
            else:
                print(f"\n  {bold('[c]')}lear  {bold('[e]')}dit Betrag  {bold('[s]')}kip")

            action = input("  → ").strip().lower()
            if action == "" and high_conf:
                action = "c"

            if action == "c":
                to_clear.append(yt["id"])
                print(ok("  ✅ Wird gecleared."))
                _offer_alias(bt["payee"], yt.get("payee_name", ""), aliases)

            elif action == "e":
                default = bt["amount"]
                raw = input(f"  Neuer Betrag [{default:+.2f} €] (Enter = Bank-Betrag übernehmen): ").strip()
                new_amt = parse_amount(raw) if raw else default
                if new_amt is not None:
                    to_update.append({
                        "id":      yt["id"],
                        "amount":  int(round(new_amt * 1000)),
                        "cleared": "cleared"
                    })
                    print(ok(f"  ✅ Betrag → {new_amt:+.2f} € und wird gecleared."))
                    _offer_alias(bt["payee"], yt.get("payee_name", ""), aliases)
                else:
                    print(warn("  ⚠️  Ungültiger Betrag – übersprungen."))
                    skipped += 1
            else:
                skipped += 1
                print(warn("  ⏭️  Übersprungen."))

        # ── Nur in YNAB ────────────────────────────────────────────────────────
        elif match["type"] == "ynab_only":
            print(f"\n  {bold('[s]')}kip/ausstehend  {bold('[c]')}lear trotzdem  "
                  f"{bold('[i]')}mmer überspringen  {dim('(Enter = skip)')}")
            action = input("  → ").strip().lower()

            if action == "c":
                to_clear.append(match["ynab"]["id"])
                print(ok("  ✅ Wird trotzdem gecleared."))
            elif action == "i":
                payee_name = match["ynab"].get("payee_name", "")
                add_deferred_payee(payee_name, config)
                skipped += 1
                print(info("  🔄 Als Dauerbucher gespeichert – wird künftig automatisch übersprungen."))
            else:
                skipped += 1
                print(info("  ⏳ Bleibt offen."))

        # ── Nur in Bank ────────────────────────────────────────────────────────
        elif match["type"] == "bank_only":
            bt = match["bank"]
            print(f"\n  {bold('[a]')}nlegen in YNAB  {bold('[s]')}kip  {dim('(Enter = skip)')}")
            action = input("  → ").strip().lower()

            if action == "a":
                tx = create_new_transaction_dialog(
                    bt, account_id, aliases, payees, categories
                )
                if tx:
                    to_create.append(tx)
                    print(ok("  ✅ Wird in YNAB angelegt."))
                else:
                    skipped += 1
                    print(warn("  ⏭️  Abgebrochen."))
            else:
                skipped += 1
                print(warn("  ⏭️  Übersprungen."))

    # ── Zusammenfassung & Bestätigung ─────────────────────────────────────────
    print(f"\n{'═' * 62}")
    print(bold(" Zusammenfassung der geplanten Änderungen:"))
    print(f"  Transaktionen clearen:      {len(to_clear)}")
    print(f"  Betrag anpassen + clearen:  {len(to_update)}")
    print(f"  Neu in YNAB anlegen:        {len(to_create)}")
    print(f"  Übersprungen/Ausstehend:    {skipped}")
    if deferred:
        print(f"  Dauerbucher (auto-skip):    {deferred}")

    if not (to_clear or to_update or to_create):
        print(info("\nKeine Änderungen – fertig."))
        return

    confirm = input(f"\n{bold('Alle Änderungen jetzt in YNAB durchführen? [j/N]:')} ").strip().lower()
    if confirm != "j":
        print(warn("Abgebrochen. Keine Änderungen vorgenommen."))
        return

    # Ausführen
    if to_clear:
        payload = {"transactions": [{"id": tid, "cleared": "cleared"} for tid in to_clear]}
        result  = api_patch(f"/budgets/{budget_id}/transactions", payload, token)
        if result:
            print(ok(f"  ✅ {len(to_clear)} Transaktionen gecleared."))

    if to_update:
        payload = {"transactions": to_update}
        result  = api_patch(f"/budgets/{budget_id}/transactions", payload, token)
        if result:
            print(ok(f"  ✅ {len(to_update)} Transaktionen aktualisiert und gecleared."))

    if to_create:
        payload = {"transactions": to_create}
        result  = api_post(f"/budgets/{budget_id}/transactions", payload, token)
        if result:
            print(ok(f"  ✅ {len(to_create)} Transaktionen neu angelegt."))

    print(ok("\n🎉 Abgleich abgeschlossen!"))

# ── Reconcile ────────────────────────────────────────────────────────────────
def _do_reconcile(budget_id: str, account_id: str, token: str) -> bool:
    """Markiert alle 'cleared' Transaktionen des Kontos als 'reconciled'."""
    txs_data    = api_get(f"/budgets/{budget_id}/accounts/{account_id}/transactions", token)
    cleared_txs = [t for t in txs_data["data"]["transactions"] if t.get("cleared") == "cleared"]

    if not cleared_txs:
        print(warn("  Keine abgeglichenen Transaktionen gefunden."))
        return False

    payload = {"transactions": [{"id": t["id"], "cleared": "reconciled"} for t in cleared_txs]}
    result  = api_patch(f"/budgets/{budget_id}/transactions", payload, token)
    if result:
        print(ok(f"  ✅ {len(cleared_txs)} Transaktionen auf 'reconciled' gesetzt."))
        print(ok("  🏦 Konto erfolgreich abgeschlossen!"))
        return True
    return False


def offer_reconcile(budget_id: str, account_id: str, account_name: str, token: str):
    """
    Zeigt nach dem Abgleich die Kontostände (YNAB cleared vs. Bank) und
    bietet Reconcile an wenn sie übereinstimmen – oder mit Ausgleichsbuchung wenn nicht.
    """
    # Frischen Kontostand direkt vom Server holen
    account_data    = api_get(f"/budgets/{budget_id}/accounts/{account_id}", token)
    account         = account_data["data"]["account"]
    cleared_balance = account["cleared_balance"] / 1000.0
    total_balance   = account["balance"] / 1000.0

    print(f"\n{'─' * 62}")
    print(bold(f" Kontostand nach Abgleich – {account_name}"))
    print(f"  YNAB abgeglichen:   {fmt_eur(cleared_balance)}")
    print(f"  YNAB gesamt:        {fmt_eur(total_balance)}")
    if abs(total_balance - cleared_balance) > 0.01:
        diff_open = total_balance - cleared_balance
        print(dim(f"  (davon noch offen:  {diff_open:+.2f} €)"))

    raw = input(f"\n  Aktueller Kontostand laut Bank {dim('(Enter = überspringen)')}: ").strip()
    if not raw:
        return

    bank_balance = parse_amount(raw)
    if bank_balance is None:
        print(warn("  ⚠️  Ungültiger Betrag – Reconcile übersprungen."))
        return

    diff = round(bank_balance - cleared_balance, 2)
    print(f"  Bank:               {fmt_eur(bank_balance)}")

    if abs(diff) < 0.01:
        # Salden stimmen überein – direkt reconcilen
        print(ok("  ✅ Salden stimmen überein!"))
        confirm = input(f"\n  {bold('Konto jetzt reconcilen? [j/N]:')} ").strip().lower()
        if confirm == "j":
            _do_reconcile(budget_id, account_id, token)
    else:
        # Differenz vorhanden
        print(warn(f"\n  ⚠️  Differenz: {diff:+.2f} €"))
        print(f"\n  {bold('[a]')}usgleichsbuchung ({diff:+.2f} €) erstellen und reconcilen")
        print(f"  {bold('[s]')}kip – kein Reconcile")
        action = input("  → ").strip().lower()

        if action == "a":
            adj = {
                "account_id": account_id,
                "date":       datetime.now().strftime("%Y-%m-%d"),
                "amount":     int(round(diff * 1000)),
                "payee_name": "Ausgleichsbuchung",
                "memo":       "Reconciliation balance adjustment",
                "cleared":    "cleared"
            }
            result = api_post(f"/budgets/{budget_id}/transactions", {"transaction": adj}, token)
            if result:
                print(ok(f"  ✅ Ausgleichsbuchung über {diff:+.2f} € angelegt."))
                _do_reconcile(budget_id, account_id, token)
        else:
            print(dim("  Reconcile übersprungen."))


# ── Hilfsfunktion: ein Konto abgleichen ──────────────────────────────────────
def run_account_reconcile(budget: dict, token: str, aliases: dict, config: dict) -> bool:
    """
    Führt den vollständigen Abgleich für ein Konto durch.
    Gibt True zurück wenn erfolgreich, False wenn abgebrochen.
    """
    # Konten mit offenen Transaktionen laden (frisch vom Server)
    accounts_data = api_get(f"/budgets/{budget['id']}/accounts", token)
    accounts = [
        a for a in accounts_data["data"]["accounts"]
        if not a.get("closed") and not a.get("deleted")
        and a.get("uncleared_balance", 0) != 0
    ]

    if not accounts:
        print(ok("\n🎉 Keine Konten mit offenen Transaktionen – alles abgeglichen!"))
        return False

    print(f"\n{bold('Konto wählen:')}  {dim('(nur Konten mit offenen Transaktionen)')}")
    for i, a in enumerate(accounts, 1):
        unc = a["uncleared_balance"] / 1000.0
        print(f"  [{i}] {a['name']:<35} offen: {fmt_eur(unc)}")
    print(f"  [{bold('q')}] Beenden")

    choice = input("\n→ ").strip().lower()
    if choice == "q":
        return False
    try:
        account = accounts[int(choice) - 1]
    except (ValueError, IndexError):
        print(err("Ungültige Auswahl."))
        return True  # Schleife fortsetzen

    print(ok(f"✅ Konto: {account['name']}"))

    # Offene YNAB-Transaktionen laden
    txs_data  = api_get(f"/budgets/{budget['id']}/accounts/{account['id']}/transactions", token)
    uncleared = [t for t in txs_data["data"]["transactions"] if t.get("cleared") == "uncleared"]

    if not uncleared:
        print(ok("\nKeine offenen Transaktionen für dieses Konto."))
        return True

    print(f"\n{bold(f'{len(uncleared)} offene YNAB-Transaktionen:')}")
    for t in sorted(uncleared, key=lambda x: x["date"]):
        print(f"  {dim(t['date'])}  {fmt_eur(t['amount']/1000):>18}  {t.get('payee_name') or '—'}")

    # Bank-Transaktionen: CSV oder manuell eingeben?
    script_dir = Path(__file__).parent
    csv_files  = find_csv_files(script_dir)

    print()
    if csv_files:
        print(bold("Kontoauszug als CSV verfügbar:"))
        for i, f in enumerate(csv_files, 1):
            print(f"  [{i}] {f.name}")
        print(f"  [m] Manuell einfügen")
        print(f"  [n] Kein Abgleich – direkt clearen")
        choice_csv = input("\n→ ").strip().lower()
    else:
        choice_csv = input(
            f"{bold('Bank-Transaktionen: [m]anuell einfügen  [n]ein (direkt clearen)')} → "
        ).strip().lower()

    if choice_csv == "n":
        confirm = input(warn("Alle offenen Transaktionen ohne Abgleich direkt clearen? [j/N]: ")).strip().lower()
        if confirm == "j":
            payload = {"transactions": [{"id": t["id"], "cleared": "cleared"} for t in uncleared]}
            api_patch(f"/budgets/{budget['id']}/transactions", payload, token)
            print(ok(f"✅ {len(uncleared)} Transaktionen gecleared."))
        return True
    elif choice_csv == "m":
        bank_txs = read_bank_transactions()
    else:
        try:
            csv_idx  = int(choice_csv) - 1
            csv_path = csv_files[csv_idx]
        except (ValueError, IndexError):
            csv_path = csv_files[0]
        print(ok(f"✅ Lade: {csv_path.name}"))
        bank_txs = parse_finanzblick_csv(str(csv_path))

    if not bank_txs:
        print(warn("Keine Bank-Transaktionen eingegeben."))
        return True

    print(ok(f"\n✅ {len(bank_txs)} Bank-Transaktionen gelesen."))

    # YNAB-Stammdaten für den Anlegen-Dialog laden
    print(dim("  Lade Payees und Kategorien aus YNAB…"), end="", flush=True)
    payees     = load_ynab_payees(budget["id"], token)
    categories = load_ynab_categories(budget["id"], token)
    print(ok(f" {len(payees)} Payees, {len(categories)} Kategorien geladen."))

    # Matching
    matches = match_transactions(uncleared, bank_txs, aliases, config)

    n_matched   = sum(1 for m in matches if m["type"] == "matched")
    n_only_ynab = sum(1 for m in matches if m["type"] == "ynab_only")
    n_deferred  = sum(1 for m in matches if m["type"] == "ynab_deferred")
    n_bank_only = sum(1 for m in matches if m["type"] == "bank_only")

    print(f"\n{bold('Matching-Ergebnis:')}")
    print(f"  {ok(str(n_matched))} Paare gefunden")
    if n_only_ynab:  print(f"  {warn(str(n_only_ynab))} nur in YNAB (ausstehend?)")
    if n_deferred:   print(f"  {dim(str(n_deferred))} Dauerbucher (automatisch übersprungen)")
    if n_bank_only:  print(f"  {err(str(n_bank_only))} nur im Konto (fehlt in YNAB)")

    input(f"\n{dim('Enter zum Starten des interaktiven Abgleichs...')}")

    interactive_reconcile(matches, budget["id"], account["id"], token,
                          aliases, config, payees, categories)

    # Nach dem Abgleich: Reconcile anbieten
    offer_reconcile(budget["id"], account["id"], account["name"], token)
    return True


# ── Hauptprogramm ─────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print(bold("  YNAB Abgleich-Tool"))
    print("=" * 62)

    token   = load_token()
    aliases = load_aliases()
    config  = load_config()

    if aliases:
        print(info(f"📖 {len(aliases)} Payee-Alias(e) geladen."))
    if config.get("deferred_payees"):
        print(info(f"🔄 {len(config['deferred_payees'])} Dauerbucher-Regel(n) aktiv."))

    # Budget wählen (archivierte ausblenden)
    budgets_data = api_get("/budgets", token)
    budgets = [b for b in budgets_data["data"]["budgets"]
               if "Archived" not in b.get("name", "")]

    if len(budgets) == 1:
        budget = budgets[0]
        print(ok(f"✅ Budget: {budget['name']}"))
    else:
        print(f"\n{bold('Budget wählen:')}")
        for i, b in enumerate(budgets, 1):
            print(f"  [{i}] {b['name']}")
        choice = input("→ ").strip()
        try:
            budget = budgets[int(choice) - 1]
        except (ValueError, IndexError):
            budget = budgets[0]
        print(ok(f"✅ Budget: {budget['name']}"))

    # Konto-Schleife: nach jedem Abgleich weiteres Konto anbieten
    while True:
        continue_loop = run_account_reconcile(budget, token, aliases, config)
        if not continue_loop:
            break
        print()
        weiter = input(bold("Weiteres Konto abgleichen? [J/n]: ")).strip().lower()
        if weiter == "n":
            break

    print(ok("\n👋 Auf Wiedersehen!"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{warn('Abgebrochen.')}")
