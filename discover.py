#!/usr/bin/env python3
"""Discovery: alle BANKSapi-Bankzugänge + YNAB-Konten auflisten.

Hilft beim Mapping zwischen BANKSapi-Konten (IBAN) und YNAB-Konten (account_id).
"""
import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv(".env")

BANKSAPI_KEY = os.environ["BANKSAPI_API_KEY"]
YNAB_TOKEN   = os.environ["YNAB_API_TOKEN"]
YNAB_BUDGET  = "e37c6afe-8939-418f-8073-0d68136a3430"

BANKSAPI_BASE = "https://banksapi.io"
YNAB_BASE     = "https://api.youneedabudget.com/v1"


def get_bearer() -> str:
    r = requests.post(f"{BANKSAPI_BASE}/onebasic/bc-token",
                      headers={"X-API-KEY": BANKSAPI_KEY})
    r.raise_for_status()
    return r.json()["access_token"]


def list_banksapi(bearer: str) -> list:
    """Antwort ist ein Dict {zugang_id: zugang_details}."""
    r = requests.get(f"{BANKSAPI_BASE}/customer/v2/bankzugaenge",
                     headers={"Authorization": f"Bearer {bearer}"})
    r.raise_for_status()
    raw = r.json()
    if isinstance(raw, dict):
        return list(raw.values())
    return raw


def list_ynab() -> list:
    r = requests.get(f"{YNAB_BASE}/budgets/{YNAB_BUDGET}/accounts",
                     headers={"Authorization": f"Bearer {YNAB_TOKEN}"})
    r.raise_for_status()
    return [a for a in r.json()["data"]["accounts"]
            if not a.get("closed") and not a.get("deleted")]


def dump_raw():
    """Dump raw JSON für Inspektion."""
    bearer = get_bearer()
    zugaenge = list_banksapi(bearer)
    print(json.dumps(zugaenge, indent=2, ensure_ascii=False))


def main():
    if "--raw" in sys.argv:
        dump_raw()
        return

    bearer = get_bearer()
    zugaenge = list_banksapi(bearer)

    print("=" * 70)
    print(f"BANKSapi Bankzugänge ({len(zugaenge)})")
    print("=" * 70)
    for z in zugaenge:
        zid     = z.get("id")
        konten  = z.get("bankprodukte") or []
        # Bank-Name aus erstem Konto ableiten (steht nicht im Zugang selbst)
        bank    = (konten[0].get("kreditinstitut") if konten else "?")
        blz     = (konten[0].get("blz") if konten else "?")
        bic     = (konten[0].get("bic") if konten else "?")
        status  = z.get("status") or "?"
        sync    = "✓" if z.get("sync") else "✗"
        print(f"\nZugang ID: {zid}")
        print(f"  Bank:    {bank}")
        print(f"  BLZ/BIC: {blz} / {bic}")
        print(f"  Status:  {status} (sync={sync})")
        for k in konten:
            kid    = k.get("id")
            iban   = k.get("iban") or "?"
            bez    = k.get("bezeichnung") or ""
            kat    = k.get("kategorie") or ""
            saldo  = k.get("saldo")
            print(f"    Konto-ID: {kid}")
            print(f"      IBAN:        {iban}")
            print(f"      Bezeichnung: {bez}  [{kat}]")
            print(f"      Saldo:       {saldo} EUR")

    accs = list_ynab()
    print("\n" + "=" * 70)
    print(f"YNAB Konten ({len(accs)} aktiv)")
    print("=" * 70)
    for a in accs:
        bal = a["balance"] / 1000.0
        print(f"  {a['id']}  {a['name']:<40}  Saldo: {bal:>10.2f} EUR")


if __name__ == "__main__":
    main()
