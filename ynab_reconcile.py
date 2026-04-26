#!/usr/bin/env python3
"""
YNAB Abgleich-Tool
Interaktiver Abgleich von YNAB-Transaktionen mit Kontoauszugsdaten (z.B. aus Finanzblick)
"""

import difflib
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.api import APIError, TokenNotFoundError, api_get, api_patch, api_post, load_token
from core.csv_parser import parse_finanzblick_csv
from core.matching import match_transactions
from core.parsing import parse_amount
from core.state import (add_deferred_payee, load_aliases, load_config,
                        resolve_alias, save_alias)
from core.ynab import load_ynab_categories, load_ynab_payees

# ── Farben ────────────────────────────────────────────────────────────────────
G   = "\033[32m"
Y   = "\033[33m"
R   = "\033[31m"
C   = "\033[36m"
W   = "\033[1m"
DIM = "\033[2m"
X   = "\033[0m"

def ok(s):   return f"{G}{s}{X}"
def warn(s): return f"{Y}{s}{X}"
def err(s):  return f"{R}{s}{X}"
def bold(s): return f"{W}{s}{X}"
def info(s): return f"{C}{s}{X}"
def dim(s):  return f"{DIM}{s}{X}"


def fmt_eur(amount_float: float, colored: bool = True) -> str:
    s = f"{amount_float:+.2f} €"
    if not colored:
        return s
    return err(s) if amount_float < 0 else ok(s)


# ── Payee-Auswahl (interaktiv) ────────────────────────────────────────────────
def fuzzy_pick_payee(query: str, payees: list) -> str:
    if not query or not payees:
        return query
    lower = query.lower()
    matches = [p for p in payees if lower in p["name"].lower()]
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
        return matches[0]["name"]

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
    if not categories:
        return None

    query = input(f"  Kategorie suchen {dim('(leer = keine Kategorie)')}: ").strip()
    if not query:
        return None

    lower = query.lower()
    matches = [c for c in categories if lower in c["display"].lower()]
    if not matches:
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
    while True:
        print()

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

        default_payee = resolve_alias(bt["payee"], aliases)
        print(f"  Payee suchen/eingeben [{dim(default_payee)}]:")
        raw_payee = input("  → ").strip()
        if not raw_payee:
            payee_name = default_payee
        else:
            payee_name = fuzzy_pick_payee(raw_payee, payees)

        category_id = pick_category(categories)

        raw_amt = input(f"  Betrag [{bt['amount']:+.2f} €] (Enter = übernehmen): ").strip()
        amount = parse_amount(raw_amt) if raw_amt else bt["amount"]
        if amount is None:
            print(warn("  ⚠️  Ungültiger Betrag, verwende Bank-Betrag."))
            amount = bt["amount"]

        memo = input("  Memo (optional): ").strip()

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
            if payee_name != bt["payee"]:
                _offer_alias(bt["payee"], payee_name, aliases)
            return tx

        elif confirm == "w":
            print(info("  🔄 Eingabe wiederholen..."))
            continue

        else:
            return None


# ── Bank-Transaktionen manuell einfügen ──────────────────────────────────────
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
        parts = None
        for sep in ["\t", ";", "|"]:
            if sep in line:
                parts = [p.strip() for p in line.split(sep, 2)]
                break
        if parts is None:
            parts = line.split(None, 2)

        if len(parts) < 2:
            skipped.append(line)
            continue

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


def find_csv_files(folder: Path) -> list:
    return sorted(folder.glob("*.csv"))


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
    if not bank_payee or not ynab_payee:
        return
    if bank_payee.lower().strip() == ynab_payee.lower().strip():
        return
    if resolve_alias(bank_payee, aliases) != bank_payee:
        return
    similarity = difflib.SequenceMatcher(None, bank_payee.lower(), ynab_payee.lower()).ratio()
    if similarity >= 0.80:
        return
    prompt = dim(f'Alias merken?  Bank: "{bank_payee}" → YNAB: "{ynab_payee}"  [j/N]:')
    ans = input(f"  {prompt} ").strip().lower()
    if ans == "j":
        save_alias(bank_payee, ynab_payee)
        aliases[bank_payee.lower().strip()] = ynab_payee
        print(ok(f"  💾 Alias gespeichert: '{bank_payee}' → '{ynab_payee}'"))


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

    to_clear  = []
    to_update = []
    to_create = []
    skipped   = 0

    active_matches   = [m for m in matches if m["type"] != "ynab_deferred"]
    deferred_matches = [m for m in matches if m["type"] == "ynab_deferred"]
    deferred = len(deferred_matches)
    if deferred:
        names = ', '.join(set(m['ynab'].get('payee_name', '?') for m in deferred_matches[:3]))
        print(info(f"\n⏭️  {deferred} Dauerbucher-Einträge automatisch übersprungen "
                   f"({names}{'…' if deferred > 3 else ''})"))

    total = len(active_matches)

    for idx, match in enumerate(active_matches, 1):
        show_match(match, idx, total)

        if match["type"] == "matched":
            yt = match["ynab"]
            bt = match["bank"]
            has_diff  = match["amount_diff"] > 0.01
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

        elif match["type"] == "ynab_only":
            print(f"\n  {bold('[s]')}kip/ausstehend  {bold('[c]')}lear trotzdem  "
                  f"{bold('[i]')}mmer überspringen  {dim('(Enter = skip)')}")
            action = input("  → ").strip().lower()

            if action == "c":
                to_clear.append(match["ynab"]["id"])
                print(ok("  ✅ Wird trotzdem gecleared."))
            elif action == "i":
                payee_name = match["ynab"].get("payee_name", "")
                keyword = add_deferred_payee(payee_name, config)
                skipped += 1
                if keyword:
                    print(ok(f"  💾 '{keyword}' als Dauerbucher gespeichert – wird künftig automatisch übersprungen."))
                else:
                    print(info("  🔄 Bereits als Dauerbucher bekannt."))
            else:
                skipped += 1
                print(info("  ⏳ Bleibt offen."))

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

    # Zusammenfassung & Bestätigung
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

    if to_clear:
        payload = {"transactions": [{"id": tid, "cleared": "cleared"} for tid in to_clear]}
        api_patch(f"/budgets/{budget_id}/transactions", payload, token)
        print(ok(f"  ✅ {len(to_clear)} Transaktionen gecleared."))

    if to_update:
        payload = {"transactions": to_update}
        api_patch(f"/budgets/{budget_id}/transactions", payload, token)
        print(ok(f"  ✅ {len(to_update)} Transaktionen aktualisiert und gecleared."))

    if to_create:
        payload = {"transactions": to_create}
        api_post(f"/budgets/{budget_id}/transactions", payload, token)
        print(ok(f"  ✅ {len(to_create)} Transaktionen neu angelegt."))

    print(ok("\n🎉 Abgleich abgeschlossen!"))


# ── Reconcile ─────────────────────────────────────────────────────────────────
def _do_reconcile(budget_id: str, account_id: str, token: str) -> bool:
    txs_data    = api_get(f"/budgets/{budget_id}/accounts/{account_id}/transactions", token)
    cleared_txs = [t for t in txs_data["data"]["transactions"] if t.get("cleared") == "cleared"]

    if not cleared_txs:
        print(warn("  Keine abgeglichenen Transaktionen gefunden."))
        return False

    payload = {"transactions": [{"id": t["id"], "cleared": "reconciled"} for t in cleared_txs]}
    api_patch(f"/budgets/{budget_id}/transactions", payload, token)
    print(ok(f"  ✅ {len(cleared_txs)} Transaktionen auf 'reconciled' gesetzt."))
    print(ok("  🏦 Konto erfolgreich abgeschlossen!"))
    return True


def offer_reconcile(budget_id: str, account_id: str, account_name: str, token: str):
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
        print(ok("  ✅ Salden stimmen überein!"))
        confirm = input(f"\n  {bold('Konto jetzt reconcilen? [j/N]:')} ").strip().lower()
        if confirm == "j":
            _do_reconcile(budget_id, account_id, token)
    else:
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
            api_post(f"/budgets/{budget_id}/transactions", {"transaction": adj}, token)
            print(ok(f"  ✅ Ausgleichsbuchung über {diff:+.2f} € angelegt."))
            _do_reconcile(budget_id, account_id, token)
        else:
            print(dim("  Reconcile übersprungen."))


# ── Hilfsfunktion: ein Konto abgleichen ──────────────────────────────────────
def run_account_reconcile(budget: dict, token: str, aliases: dict, config: dict) -> bool:
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
        return True

    print(ok(f"✅ Konto: {account['name']}"))

    txs_data  = api_get(f"/budgets/{budget['id']}/accounts/{account['id']}/transactions", token)
    uncleared = [t for t in txs_data["data"]["transactions"] if t.get("cleared") == "uncleared"]

    if not uncleared:
        print(ok("\nKeine offenen Transaktionen für dieses Konto."))
        return True

    print(f"\n{bold(f'{len(uncleared)} offene YNAB-Transaktionen:')}")
    for t in sorted(uncleared, key=lambda x: x["date"]):
        print(f"  {dim(t['date'])}  {fmt_eur(t['amount']/1000):>18}  {t.get('payee_name') or '—'}")

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
        bank_txs, skipped = parse_finanzblick_csv(str(csv_path))
        if skipped:
            print(warn(f"⚠️  {len(skipped)} Zeile(n) übersprungen (nicht parsbar)."))

    if not bank_txs:
        print(warn("Keine Bank-Transaktionen eingegeben."))
        return True

    print(ok(f"\n✅ {len(bank_txs)} Bank-Transaktionen gelesen."))

    print(dim("  Lade Payees und Kategorien aus YNAB…"), end="", flush=True)
    payees     = load_ynab_payees(budget["id"], token)
    categories = load_ynab_categories(budget["id"], token)
    print(ok(f" {len(payees)} Payees, {len(categories)} Kategorien geladen."))

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

    offer_reconcile(budget["id"], account["id"], account["name"], token)
    return True


# ── Hauptprogramm ─────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print(bold("  YNAB Abgleich-Tool"))
    print("=" * 62)

    try:
        token = load_token()
    except TokenNotFoundError as e:
        print(err(f"❌ {e}"))
        sys.exit(1)

    aliases = load_aliases()
    config  = load_config()

    if aliases:
        print(info(f"📖 {len(aliases)} Payee-Alias(e) geladen."))
    if config.get("deferred_payees"):
        print(info(f"🔄 {len(config['deferred_payees'])} Dauerbucher-Regel(n) aktiv."))

    try:
        budgets_data = api_get("/budgets", token)
    except APIError as e:
        print(err(f"❌ API-Fehler: {e}"))
        sys.exit(1)

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

    try:
        while True:
            continue_loop = run_account_reconcile(budget, token, aliases, config)
            if not continue_loop:
                break
            print()
            weiter = input(bold("Weiteres Konto abgleichen? [J/n]: ")).strip().lower()
            if weiter == "n":
                break
    except APIError as e:
        print(err(f"\n❌ API-Fehler: {e}"))
        sys.exit(1)

    print(ok("\n👋 Auf Wiedersehen!"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(warn("\n\nAbgebrochen."))
        sys.exit(0)
