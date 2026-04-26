import difflib
from datetime import datetime

from .state import is_deferred_payee, resolve_alias


def match_transactions(ynab_txs: list, bank_txs: list,
                       aliases: dict = None, config: dict = None) -> list:
    """
    Versucht, YNAB- und Bank-Transaktionen zu paaren.
    Rückgabe: Liste von Match-Dicts mit type='matched'|'ynab_only'|'ynab_deferred'|'bank_only'
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
            resolved    = resolve_alias(bt["payee"], aliases)
            bank_payee  = resolved.lower()

            # Betrags-Score (wichtigster Faktor)
            amount_diff = abs(ynab_amount - bank_amount)
            rel_diff    = amount_diff / max(abs(ynab_amount), 0.01)
            if amount_diff < 0.01:
                amount_score = 1.0
            elif amount_diff < 0.05:
                amount_score = 0.95
            elif rel_diff < 0.05:
                amount_score = 0.75
            elif rel_diff < 0.15:
                amount_score = 0.40
            else:
                continue

            # Datum-Score
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

            # Payee-Score
            payee_score = difflib.SequenceMatcher(
                None, ynab_payee, bank_payee
            ).ratio()

            total = amount_score * 0.60 + date_score * 0.25 + payee_score * 0.15

            if total > best_score:
                best_score = total
                best_bi    = bi

        if best_bi is not None and best_score > 0.3:
            if best_bi in {v[0] for v in matched_ynab.values()}:
                existing_yi = next(k for k, v in matched_ynab.items() if v[0] == best_bi)
                if best_score > matched_ynab[existing_yi][1]:
                    del matched_ynab[existing_yi]
                    matched_ynab[yi] = (best_bi, best_score)
            else:
                matched_ynab[yi] = (best_bi, best_score)

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

    def sort_key(r):
        if r["type"] == "matched":   return (0, -r["score"])
        if r["type"] == "ynab_only": return (1, 0)
        return (2, 0)

    results.sort(key=sort_key)
    return results
