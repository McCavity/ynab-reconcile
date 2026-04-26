import csv
import re
from datetime import datetime
from typing import Optional

from .parsing import parse_amount


def _extract_payee_from_csv_row(empfaenger: str, verwendungszweck: str, abweichend: str) -> str:
    """
    Ermittelt den besten Payee-Namen aus einer Finanzblick-CSV-Zeile.
    Priorität:
      1. Für PayPal: echten Händler aus dem Verwendungszweck extrahieren
      2. AbweichenderEmpfaenger (wenn vorhanden)
      3. Empfaenger
    """
    if "paypal" in empfaenger.lower():
        m = re.search(r'/\.\s+(.+?),\s*(?:Ihr Einkauf|EREF|MREF)', verwendungszweck)
        if m:
            return m.group(1).strip()
    if abweichend.strip():
        return abweichend.strip()
    return empfaenger.strip()


def parse_finanzblick_csv(filepath: str) -> tuple:
    """
    Liest eine Buchungsliste-CSV aus Finanzblick.
    Gibt (transactions: list, skipped: list) zurück.
    """
    transactions = []
    skipped = []

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            date_str = row.get("Buchungsdatum", "").strip()
            parsed_date = None
            for fmt in ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"]:
                try:
                    parsed_date = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
            if not parsed_date:
                skipped.append(date_str or "(leer)")
                continue

            amount = parse_amount(row.get("Betrag", ""))
            if amount is None:
                skipped.append(row.get("Betrag", "?"))
                continue

            payee = _extract_payee_from_csv_row(
                empfaenger       = row.get("Empfaenger", ""),
                verwendungszweck = row.get("Verwendungszweck", ""),
                abweichend       = row.get("AbweichenderEmpfaenger", "")
            )

            transactions.append({
                "date":   parsed_date,
                "amount": amount,
                "payee":  payee,
                "memo":   row.get("Verwendungszweck", "").strip()[:100],
                "raw":    f"{date_str} {amount:+.2f} {payee}"
            })

    return transactions, skipped
