#!/usr/bin/env python3
"""BANKSapi → YNAB Reconcile.

Matching-Strategie (kein import_id — kein YNAB-Auto-Matching):
  A  reconciled in YNAB + Bank-Match  → Hash als Memo-Präfix eintragen
  B  cleared in YNAB   + Bank-Match  → Hash als Memo-Präfix eintragen
  C1 uncleared in YNAB + Bank-Match  → Hash + cleared setzen
  C2 nur in Bank                     → neue YNAB-Buchung anlegen
  D  uncleared in YNAB, kein Match   → unberührt lassen (Sammelliste)

Hash-Format im Memo: "[ba:<8 Zeichen>] <bisheriges Memo>"
Bereits verarbeitete Einträge (Memo beginnt mit "[ba:") werden übersprungen.
"""

import json
import os
import re
import sys
from datetime import date, datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from core.matching import match_transactions
from core.state import load_aliases, load_config

load_dotenv(dotenv_path=".env")

BANKSAPI_KEY    = os.environ["BANKSAPI_API_KEY"]
YNAB_TOKEN      = os.environ["YNAB_API_TOKEN"]
YNAB_BUDGET     = "e37c6afe-8939-418f-8073-0d68136a3430"

# Konto-Konfiguration: pro Konto die zugehörige BANKSapi-Zugang-ID + IBAN/Konto-ID + YNAB-Konto-ID.
# Depots werden bewusst ausgelassen — Wertpapier-Käufe/-Verkäufe erscheinen im Referenzkonto.
ACCOUNTS = [
    # Frankfurter Volksbank (Zugang 83343522-...)
    {
        "name":      "FVB *2090",
        "zugang_id": "83343522-e283-4ffb-9dff-859cdf718ab7",
        "konto_id":  "DE87501900006501012090",
        "ynab_id":   "5a70c272-c1f8-4c1d-a7f8-2101afdcefc2",
    },
    {
        "name":      "FVB *8745",
        "zugang_id": "83343522-e283-4ffb-9dff-859cdf718ab7",
        "konto_id":  "DE70501900000002508745",
        "ynab_id":   "ad848d34-a1a1-41b8-b77f-4a22fed85fca",
    },
    {
        "name":      "FVB *2549 (Kreditkarte)",
        "zugang_id": "83343522-e283-4ffb-9dff-859cdf718ab7",
        "konto_id":  "DE23501900003864381455",
        "ynab_id":   "a04b5109-fdf2-4868-a0fc-b7849604af80",
    },
    # Trade Republic (Zugang 6fd647f4-...)
    {
        "name":      "T.Republic Cash *3601",
        "zugang_id": "6fd647f4-1a24-4e08-958e-83402df26ffe",
        "konto_id":  "DE27100123450140763601",
        "ynab_id":   "4e8070ae-8034-4d25-af80-20f2c8f525d5",
    },
    # Zero / Baader Bank (Zugang 100000a7-...)
    {
        "name":      "Zero *6000",
        "zugang_id": "100000a7-56b2-4bc3-945d-d0e5c5521e4f",
        "konto_id":  "DE18700331005278436000",
        "ynab_id":   "1aa672e5-8eae-42a3-bd21-bc494f09f498",
    },
]

HASH_PREFIX_RE  = re.compile(r"^\[ba:[0-9a-f]{8}\]")
MEMO_DATE_RE    = re.compile(r"^\s*(\d{8})\b")   # YYYYMMDD am Memo-Anfang
PAYPAL_MERCH_RE = re.compile(r"(?:PP\.\d+\.PP/\.\s*|(?<=/)\.\s*)(.+?)(?:,\s*Ihr\b|,\s*EREF\b|$)")

SCORE_AUTO     = 0.78   # Automatisch matchen (exakter Betrag + ≤3 Tage reicht)
SCORE_REVIEW   = 0.50   # Zur manuellen Prüfung
MEMO_DATE_BOOST = 0.15  # Boost wenn YNAB-Memo-Datum = BANKSapi-Buchungsdatum

BANKSAPI_BASE = "https://banksapi.io"
YNAB_BASE     = "https://api.youneedabudget.com/v1"


# ---------------------------------------------------------------------------
# API-Clients
# ---------------------------------------------------------------------------

def get_bearer_token() -> str:
    r = requests.post(
        f"{BANKSAPI_BASE}/onebasic/bc-token",
        headers={"X-API-KEY": BANKSAPI_KEY},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_banksapi_txs(token: str, zugang_id: str, konto_id: str,
                       since_date: Optional[str] = None) -> list[dict]:
    """BANKSapi liefert immer den vollen verfügbaren Zeitraum — clientseitig filtern."""
    r = requests.get(
        f"{BANKSAPI_BASE}/customer/v2/bankzugaenge/{zugang_id}/{konto_id}/kontoumsaetze",
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return []
    if since_date:
        data = [tx for tx in data if (tx.get("buchungsdatum") or "")[:10] >= since_date]
    return data


def fetch_ynab_txs(account_id: str, since_date: Optional[str] = None) -> list[dict]:
    params = {}
    if since_date:
        params["since_date"] = since_date
    r = requests.get(
        f"{YNAB_BASE}/budgets/{YNAB_BUDGET}/accounts/{account_id}/transactions",
        headers={"Authorization": f"Bearer {YNAB_TOKEN}"},
        params=params,
    )
    r.raise_for_status()
    return [t for t in r.json()["data"]["transactions"] if not t.get("deleted")]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def memo_prefix(hash_str: str) -> str:
    return f"[ba:{hash_str[:8]}]"


def already_tagged(tx: dict) -> bool:
    return bool(HASH_PREFIX_RE.match(tx.get("memo") or ""))


def build_new_memo(hash_str: str, old_memo: Optional[str]) -> str:
    prefix = memo_prefix(hash_str)
    if old_memo:
        return f"{prefix} {old_memo}"
    return prefix


def extract_memo_date(memo: str) -> Optional[date]:
    """Extrahiert YYYYMMDD-Timestamp vom Memo-Anfang (manuell vom Nutzer eingetragen)."""
    if not memo:
        return None
    m = MEMO_DATE_RE.match(memo)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def extract_paypal_merchant(zweck: str) -> Optional[str]:
    """Extrahiert echten Händlernamen aus PayPal-Verwendungszweck."""
    m = PAYPAL_MERCH_RE.search(zweck)
    if m:
        name = m.group(1).strip().rstrip(",")
        if len(name) > 2:
            return name
    return None


def boost_matches(results: list[dict]) -> list[dict]:
    """Erhöht Score für Matches mit zusätzlichen Signalen."""
    for r in results:
        if r["type"] != "matched":
            continue
        yt   = r["ynab"]
        bt   = r["bank"]
        boost_reasons = []

        # Memo-Datum-Boost: YNAB-Memo enthält Buchungsdatum der Bank
        memo_date = extract_memo_date(yt.get("memo") or "")
        if memo_date and memo_date == bt["date"]:
            r["score"] = min(r["score"] + MEMO_DATE_BOOST, 1.0)
            boost_reasons.append("memo_date")

        if boost_reasons:
            r["boost"] = boost_reasons

    return results


def banksapi_to_matching_fmt(tx: dict) -> dict:
    """BANKSapi-Transaktion → Format das matching.py erwartet.
    Bei PayPal wird der echte Händlername aus dem Verwendungszweck extrahiert.
    """
    raw_payee = (tx.get("gegenkontoInhaber") or "").strip()
    zweck     = (tx.get("verwendungszweck") or "").strip()

    if "paypal" in raw_payee.lower() and zweck:
        merchant = extract_paypal_merchant(zweck)
        effective_payee = merchant if merchant else raw_payee
    else:
        effective_payee = raw_payee

    return {
        "amount":    tx["betrag"],
        "date":      datetime.strptime(tx["buchungsdatum"][:10], "%Y-%m-%d").date(),
        "payee":     effective_payee,
        "iban":      (tx.get("gegenkontoIban") or "").strip(),
        "zweck":     zweck,
        "hash":      tx["hash"],
        "_raw":      tx,
    }


def ynab_to_matching_fmt(tx: dict) -> dict:
    """YNAB-Transaktion → Format das matching.py erwartet.
    matching.py liest 'payee_name' (so wie YNAB-Roh-API auch) — Schlüsselname beibehalten.
    """
    return {
        "amount":      tx["amount"],  # Milliunits — matching.py teilt intern durch 1000
        "date":        tx["date"],
        "payee_name":  (tx.get("payee_name") or "").strip(),
        "memo":        (tx.get("memo") or "").strip(),
        "cleared":     tx.get("cleared", "uncleared"),
        "is_transfer": bool(tx.get("transfer_account_id")),
        "_raw":        tx,
    }


def patch_ynab_tx(tx_id: str, updates: dict) -> bool:
    r = requests.patch(
        f"{YNAB_BASE}/budgets/{YNAB_BUDGET}/transactions/{tx_id}",
        headers={
            "Authorization":  f"Bearer {YNAB_TOKEN}",
            "Content-Type":   "application/json",
        },
        json={"transaction": updates},
    )
    return r.status_code == 200


def delete_ynab_tx(tx_id: str) -> bool:
    r = requests.delete(
        f"{YNAB_BASE}/budgets/{YNAB_BUDGET}/transactions/{tx_id}",
        headers={"Authorization": f"Bearer {YNAB_TOKEN}"},
    )
    return r.status_code in (200, 204)


def prompt_review_decisions(reviews: list, account_name: str) -> list[str]:
    """Fragt für jeden Review-Eintrag eine Entscheidung ab.
    Rückgabe: Liste mit 'm'/'a'/'n'/'d'/'s' in derselben Reihenfolge wie reviews.
    """
    decisions = []
    if not reviews:
        return decisions
    print(f"\n{'='*60}")
    print(f"  REVIEW: {account_name} — {len(reviews)} unsichere Matches")
    print(f"{'='*60}")
    for i, m in enumerate(reviews, 1):
        yt = m["ynab"]["_raw"]
        bt = m["bank"]
        print(f"\n[{i}/{len(reviews)}] Score {m['score']:.2f} | Diff {m['amount_diff']:.2f} EUR")
        print(f"  YNAB: {yt['date']}  {yt['amount']/1000:+8.2f}  {(yt.get('payee_name') or '')[:35]}")
        print(f"        memo={(yt.get('memo') or '')[:60]!r}")
        print(f"  Bank: {bt['date']}  {bt['amount']:+8.2f}  {bt['payee'][:35]}")
        print(f"        zweck={bt['zweck'][:60]!r}")
        while True:
            ans = input("  [m]atch / [a]djust to bank amount / [n]o match / [d]elete YNAB / [s]kip → ").strip().lower()
            if ans in ("m", "a", "n", "d", "s"):
                decisions.append(ans)
                break
            print("  Bitte m, a, n, d oder s eingeben.")
    return decisions


def create_ynab_tx(account_id: str, bank_tx: dict) -> bool:
    payload = {
        "account_id":  account_id,
        "date":        bank_tx["date"].isoformat(),
        "amount":      round(bank_tx["amount"] * 1000),
        "payee_name":  bank_tx["payee"][:200] if bank_tx["payee"] else None,
        "memo":        build_new_memo(bank_tx["hash"], bank_tx["zweck"][:180] if bank_tx["zweck"] else None),
        "cleared":     "uncleared",
        "approved":    True,
    }
    r = requests.post(
        f"{YNAB_BASE}/budgets/{YNAB_BUDGET}/transactions",
        headers={
            "Authorization":  f"Bearer {YNAB_TOKEN}",
            "Content-Type":   "application/json",
        },
        json={"transaction": payload},
    )
    return r.status_code == 201


# ---------------------------------------------------------------------------
# Hauptlogik
# ---------------------------------------------------------------------------

def process_account(account: dict, bearer: str, dry_run: bool,
                    since_date: Optional[str] = None,
                    internal_ibans: Optional[set] = None) -> dict:
    name      = account["name"]
    zugang_id = account["zugang_id"]
    konto_id  = account["konto_id"]
    acc_id    = account["ynab_id"]

    print(f"\n{'='*60}")
    print(f"  {name}  ({konto_id})")
    if since_date:
        print(f"  Zeitraum: ab {since_date}")
    print(f"{'='*60}")

    # Daten laden
    raw_bank = fetch_banksapi_txs(bearer, zugang_id, konto_id, since_date=since_date)
    raw_ynab = fetch_ynab_txs(acc_id, since_date=since_date)

    # Transfers und bereits getaggte YNAB-Einträge herausfiltern
    ynab_skip    = [t for t in raw_ynab if already_tagged(t) or t.get("transfer_account_id")]
    ynab_work    = [t for t in raw_ynab if not already_tagged(t) and not t.get("transfer_account_id")]

    # Bank-Einträge mit interner Gegenkonto-IBAN vorab aussortieren — sie sollen
    # nicht gegen "normale" YNAB-Buchungen matchen (Stichwort doppelter Sparplan)
    internal = internal_ibans or set()
    bank_internal = []
    bank_work     = []
    for tx in raw_bank:
        ci = (tx.get("gegenkontoIban") or "").strip()
        if ci and ci in internal:
            bank_internal.append(tx)
        else:
            bank_work.append(tx)

    print(f"  BANKSapi: {len(raw_bank)} Buchungen ({len(bank_internal)} interne Transfers vorab abgezweigt)")
    print(f"  YNAB:     {len(raw_ynab)} Buchungen total, {len(ynab_skip)} übersprungen (bereits getaggt/Transfer), {len(ynab_work)} zu verarbeiten")

    # Aliases + Config laden (Telefonica → O2 etc., deferred payees)
    aliases = load_aliases()
    config  = load_config()

    # Matching-Format aufbereiten
    ynab_fmt = [ynab_to_matching_fmt(t) for t in ynab_work]
    bank_fmt = [banksapi_to_matching_fmt(t) for t in bank_work]

    results  = boost_matches(match_transactions(ynab_fmt, bank_fmt, aliases, config))

    # Ergebnisse kategorisieren
    auto_matches    = []  # Score >= SCORE_AUTO
    review_matches  = []  # SCORE_REVIEW <= Score < SCORE_AUTO
    ynab_only       = []  # D: kein Bank-Match, uncleared
    bank_only       = []  # C2: kein YNAB-Match (extern)
    transfer_review = []  # Bank-Only mit Gegenkonto-IBAN aus internem Konto

    # Vorab abgezweigte interne Transfers in Review-Liste übernehmen
    for tx in bank_internal:
        bf = banksapi_to_matching_fmt(tx)
        transfer_review.append({
            "type":         "internal_transfer",
            "bank":         bf,
            "counter_iban": (tx.get("gegenkontoIban") or "").strip(),
        })

    for r in results:
        if r["type"] == "matched":
            if r["score"] >= SCORE_AUTO:
                auto_matches.append(r)
            else:
                review_matches.append(r)
        elif r["type"] in ("ynab_only", "ynab_deferred"):
            if r["ynab"]["cleared"] == "uncleared":
                ynab_only.append(r)
        elif r["type"] == "bank_only":
            bank_only.append(r)

    print(f"\n  Matching-Ergebnis:")
    print(f"    Auto-Match  (≥{SCORE_AUTO:.0%}): {len(auto_matches)}")
    print(f"    Prüfen      ({SCORE_REVIEW:.0%}–{SCORE_AUTO:.0%}): {len(review_matches)}")
    print(f"    Nur YNAB    (D, uncleared):   {len(ynab_only)}")
    print(f"    Nur Bank    (C2, neu anlegen): {len(bank_only)}")
    print(f"    Interne Transfers (Review):   {len(transfer_review)}")

    # Nur im Live-Modus interaktiv durch Reviews gehen
    delete_ynab_ids = []
    if not dry_run and review_matches:
        decisions = prompt_review_decisions(review_matches, name)
        deferred_review = []
        for r, dec in zip(review_matches, decisions):
            if dec == "m":
                auto_matches.append(r)
            elif dec == "a":
                r["adjust_amount"] = True
                auto_matches.append(r)
            elif dec == "n":
                bank_only.append({"type": "bank_only", "ynab": None,
                                  "bank": r["bank"], "score": 0})
                # YNAB-Eintrag bleibt unverändert (kein ynab_only-Eintrag nötig
                # — Eintrag steht ja schon in YNAB, nichts zu tun)
            elif dec == "d":
                delete_ynab_ids.append(r["ynab"]["_raw"]["id"])
                bank_only.append({"type": "bank_only", "ynab": None,
                                  "bank": r["bank"], "score": 0})
            elif dec == "s":
                deferred_review.append(r)
        review_matches = deferred_review
        print(f"\n  Nach Review-Entscheidungen: {len(auto_matches)} Auto-Matches, "
              f"{len(bank_only)} Bank-Only, {len(delete_ynab_ids)} zu löschen, "
              f"{len(review_matches)} aufgeschoben")

    stats = {
        "tagged": 0, "created": 0, "errors": 0, "deleted": 0,
        "review": review_matches,
        "ynab_only": ynab_only,
        "transfers": transfer_review,
    }

    # --- Auto-Matches verarbeiten (A, B, C1) ---
    print(f"\n  Verarbeite {len(auto_matches)} Auto-Matches...")
    for m in auto_matches:
        yt  = m["ynab"]["_raw"]
        bt  = m["bank"]
        new_memo    = build_new_memo(bt["hash"], yt.get("memo") or None)
        updates     = {"memo": new_memo}
        cleared_was = m["ynab"]["cleared"]

        if cleared_was == "uncleared":
            updates["cleared"] = "cleared"  # C1

        if m.get("adjust_amount"):
            updates["amount"] = round(bt["amount"] * 1000)

        if dry_run:
            action = "C1" if cleared_was == "uncleared" else ("A" if cleared_was == "reconciled" else "B")
            adj = " [ADJ→{:+.2f}]".format(bt["amount"]) if m.get("adjust_amount") else ""
            print(f"    [{action}] DRY {yt['date']}  {yt['amount']/1000:+7.2f}  {(yt.get('payee_name') or '')[:25]:<25}  score={m['score']:.2f}{adj}")
        else:
            ok = patch_ynab_tx(yt["id"], updates)
            if ok:
                stats["tagged"] += 1
            else:
                stats["errors"] += 1
                print(f"    FEHLER: {yt['id']}")

    # --- Delete-Aktionen aus Review ---
    if delete_ynab_ids:
        print(f"\n  Lösche {len(delete_ynab_ids)} YNAB-Einträge (Review-Entscheidung [d])...")
        for tx_id in delete_ynab_ids:
            ok = delete_ynab_tx(tx_id)
            if ok:
                stats["deleted"] += 1
            else:
                stats["errors"] += 1
                print(f"    FEHLER beim Löschen: {tx_id}")

    # --- Bank-only (C2): neue YNAB-Buchungen ---
    print(f"\n  Lege {len(bank_only)} neue YNAB-Buchungen an (C2)...")
    for m in bank_only:
        bt = m["bank"]
        if dry_run:
            print(f"    [C2] DRY {bt['date']}  {bt['amount']:+7.2f}  {bt['payee'][:25]:<25}  hash={bt['hash'][:8]}")
        else:
            ok = create_ynab_tx(acc_id, bt)
            if ok:
                stats["created"] += 1
            else:
                stats["errors"] += 1
                print(f"    FEHLER: {bt['hash']}")

    return stats


def print_review_list(all_review: list, all_ynab_only: list, all_transfers: list):
    if all_review:
        print(f"\n{'='*60}")
        print(f"  ZUR PRÜFUNG: {len(all_review)} unsichere Matches")
        print(f"{'='*60}")
        for m in all_review:
            yt = m["ynab"]["_raw"]
            bt = m["bank"]
            print(f"\n  Score {m['score']:.2f} | Betrag-Diff: {m['amount_diff']:.2f} EUR")
            print(f"    YNAB: {yt['date']}  {yt['amount']/1000:+8.2f}  {(yt.get('payee_name') or '')[:35]}")
            print(f"    Bank: {bt['date']}  {bt['amount']:+8.2f}  {bt['payee'][:35]}")
            print(f"    Zweck: {bt['zweck'][:60]}")

    if all_ynab_only:
        print(f"\n{'='*60}")
        print(f"  NUR IN YNAB (D): {len(all_ynab_only)} uncleared, kein Bank-Match")
        print(f"{'='*60}")
        for m in all_ynab_only:
            yt = m["ynab"]["_raw"]
            print(f"    {yt['date']}  {yt['amount']/1000:+8.2f}  {(yt.get('payee_name') or '')[:35]}  memo={repr(yt.get('memo') or '')}")

    if all_transfers:
        print(f"\n{'='*60}")
        print(f"  INTERNE TRANSFERS: {len(all_transfers)} Bank-Buchungen mit Gegenkonto in unserer Liste")
        print(f"  (NICHT auto-angelegt — bitte manuell als YNAB-Transfer pflegen oder warten bis das andere Konto gesynced wird)")
        print(f"{'='*60}")
        for m in all_transfers:
            bt = m["bank"]
            ci = m.get("counter_iban", "?")
            print(f"    {bt['date']}  {bt['amount']:+8.2f}  → {ci}  {bt['payee'][:30]}  {bt['zweck'][:40]}")


def parse_arg(flag: str, default: Optional[str] = None) -> Optional[str]:
    """Liest --flag=value aus sys.argv (oder gibt default zurück)."""
    prefix = f"{flag}="
    for arg in sys.argv[1:]:
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return default


def main():
    dry_run    = "--dry-run" in sys.argv or "-n" in sys.argv
    since_date = parse_arg("--since", "2026-04-03")
    only_name  = parse_arg("--account")  # z.B. "FVB *2090"
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"BANKSapi → YNAB Reconcile  [{date.today()}]  [{mode}]  [since={since_date}]")

    selected = ACCOUNTS
    if only_name:
        selected = [a for a in ACCOUNTS if a["name"] == only_name]
        if not selected:
            print(f"FEHLER: Konto '{only_name}' nicht gefunden. Verfügbar:")
            for a in ACCOUNTS:
                print(f"  - {a['name']}")
            sys.exit(1)

    # Set aller IBANs aus unserer Konten-Konfiguration (für interne-Transfer-Detektion)
    internal_ibans = {a["konto_id"] for a in ACCOUNTS if a["konto_id"].startswith("DE")}

    print("Bearer-Token holen...")
    bearer = get_bearer_token()

    total_tagged  = 0
    total_created = 0
    total_deleted = 0
    total_errors  = 0
    all_review    = []
    all_ynab_only = []
    all_transfers = []

    for account in selected:
        stats = process_account(account, bearer, dry_run,
                                since_date=since_date,
                                internal_ibans=internal_ibans)
        total_tagged  += stats["tagged"]
        total_created += stats["created"]
        total_deleted += stats.get("deleted", 0)
        total_errors  += stats["errors"]
        all_review    += stats["review"]
        all_ynab_only += stats["ynab_only"]
        all_transfers += stats["transfers"]

    print_review_list(all_review, all_ynab_only, all_transfers)

    print(f"\n{'='*60}")
    print(f"  Gesamt: {total_tagged} getaggt, {total_created} neu angelegt, "
          f"{total_deleted} gelöscht, {total_errors} Fehler")
    if dry_run:
        cmd = "python3 banksapi_sync.py"
        if only_name:
            cmd += f' --account="{only_name}"'
        print(f"  → Zum Ausführen: {cmd}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
