"""
YNAB Reconcile – Flask Web App
"""

import io
import os
import uuid
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

from core.api import APIError, TokenNotFoundError, api_get, api_patch, api_post, load_token
from core.csv_parser import parse_finanzblick_csv
from core.matching import match_transactions
from core.parsing import parse_amount
from core.state import (add_deferred_payee, load_aliases, load_config,
                        resolve_alias, save_alias)
from core.ynab import load_ynab_categories, load_ynab_payees

app = Flask(__name__, static_folder="static", static_url_path="")

# In-memory sessions: session_id → { token, budget_id, account_id, matches,
#                                     aliases, config, payees, categories }
_sessions: dict = {}


def _session(session_id: str) -> dict:
    """Returns session dict or raises KeyError."""
    return _sessions[session_id]


def _serialize_match(m: dict) -> dict:
    """Convert a match dict to a JSON-serialisable form."""
    result = {"type": m["type"], "score": round(m.get("score", 0), 3)}

    if m.get("ynab"):
        yt = m["ynab"]
        result["ynab"] = {
            "id":         yt["id"],
            "date":       yt["date"],
            "amount":     yt["amount"] / 1000.0,
            "payee_name": yt.get("payee_name") or "",
            "memo":       yt.get("memo") or "",
        }

    if m.get("bank"):
        bt = m["bank"]
        result["bank"] = {
            "date":   bt["date"].isoformat() if hasattr(bt["date"], "isoformat") else bt["date"],
            "amount": bt["amount"],
            "payee":  bt["payee"],
            "memo":   bt.get("memo") or "",
        }

    if m["type"] == "matched":
        result["amount_diff"] = round(m.get("amount_diff", 0), 2)

    return result


# ── Static ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── Setup: budgets & accounts ─────────────────────────────────────────────────

@app.route("/api/budgets")
def get_budgets():
    try:
        token = load_token()
    except TokenNotFoundError as e:
        return jsonify({"error": str(e)}), 500

    try:
        data = api_get("/budgets", token)
    except APIError as e:
        return jsonify({"error": str(e)}), 502

    budgets = [
        {"id": b["id"], "name": b["name"]}
        for b in data["data"]["budgets"]
        if "Archived" not in b.get("name", "")
    ]
    return jsonify({"budgets": budgets})


@app.route("/api/transactions")
def get_transactions():
    budget_id  = request.args.get("budget_id")
    account_id = request.args.get("account_id")
    if not budget_id or not account_id:
        return jsonify({"error": "budget_id and account_id required"}), 400

    try:
        token = load_token()
        data  = api_get(f"/budgets/{budget_id}/accounts/{account_id}/transactions", token)
    except (TokenNotFoundError, APIError) as e:
        return jsonify({"error": str(e)}), 502

    txs      = data["data"]["transactions"]
    all_open = [t for t in txs if t.get("cleared") != "reconciled"]
    cleared  = [t for t in all_open if t.get("cleared") == "cleared"]
    uncleared = [t for t in all_open if t.get("cleared") == "uncleared"]

    def serialize(t):
        return {
            "id":         t["id"],
            "date":       t["date"],
            "amount":     t["amount"] / 1000.0,
            "payee_name": t.get("payee_name") or "",
            "memo":       t.get("memo") or "",
            "cleared":    t.get("cleared", "uncleared"),
        }

    return jsonify({
        "transactions": {
            "all":       [serialize(t) for t in sorted(all_open,  key=lambda x: x["date"], reverse=True)],
            "cleared":   [serialize(t) for t in sorted(cleared,   key=lambda x: x["date"], reverse=True)],
            "uncleared": [serialize(t) for t in sorted(uncleared, key=lambda x: x["date"], reverse=True)],
        },
        "counts": {
            "all":       len(all_open),
            "cleared":   len(cleared),
            "uncleared": len(uncleared),
        }
    })


@app.route("/api/accounts")
def get_accounts():
    budget_id = request.args.get("budget_id")
    if not budget_id:
        return jsonify({"error": "budget_id required"}), 400

    try:
        token = load_token()
        data  = api_get(f"/budgets/{budget_id}/accounts", token)
    except (TokenNotFoundError, APIError) as e:
        return jsonify({"error": str(e)}), 502

    accounts = [
        {
            "id":               a["id"],
            "name":             a["name"],
            "uncleared_balance": a["uncleared_balance"] / 1000.0,
        }
        for a in data["data"]["accounts"]
        if not a.get("closed") and not a.get("deleted")
        and a.get("uncleared_balance", 0) != 0
    ]
    return jsonify({"accounts": accounts})


# ── Session: start reconciliation ─────────────────────────────────────────────

@app.route("/api/session/start", methods=["POST"])
def start_session():
    """
    Accepts multipart/form-data:
      - budget_id  (str)
      - account_id (str)
      - csv_file   (file, Finanzblick CSV)

    Returns session_id + full match list.
    """
    budget_id  = request.form.get("budget_id")
    account_id = request.form.get("account_id")
    csv_file   = request.files.get("csv_file")

    if not budget_id or not account_id or not csv_file:
        return jsonify({"error": "budget_id, account_id und csv_file sind erforderlich"}), 400

    try:
        token = load_token()
    except TokenNotFoundError as e:
        return jsonify({"error": str(e)}), 500

    # Load uncleared YNAB transactions
    try:
        txs_data = api_get(f"/budgets/{budget_id}/accounts/{account_id}/transactions", token)
    except APIError as e:
        return jsonify({"error": str(e)}), 502

    uncleared = [t for t in txs_data["data"]["transactions"] if t.get("cleared") == "uncleared"]

    # Parse uploaded CSV (in memory, never written to disk)
    csv_text = csv_file.stream.read().decode("utf-8-sig")
    bank_txs, skipped = parse_finanzblick_csv_from_string(csv_text)

    # Load aliases + config + YNAB master data
    aliases    = load_aliases()
    config     = load_config()
    try:
        payees     = load_ynab_payees(budget_id, token)
        categories = load_ynab_categories(budget_id, token)
    except APIError as e:
        return jsonify({"error": str(e)}), 502

    # Run matching
    matches = match_transactions(uncleared, bank_txs, aliases, config)

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "token":      token,
        "budget_id":  budget_id,
        "account_id": account_id,
        "matches":    matches,
        "aliases":    aliases,
        "config":     config,
        "payees":     payees,
        "categories": categories,
    }

    return jsonify({
        "session_id": session_id,
        "matches":    [_serialize_match(m) for m in matches],
        "warnings":   skipped,
        "stats": {
            "matched":   sum(1 for m in matches if m["type"] == "matched"),
            "ynab_only": sum(1 for m in matches if m["type"] == "ynab_only"),
            "deferred":  sum(1 for m in matches if m["type"] == "ynab_deferred"),
            "bank_only": sum(1 for m in matches if m["type"] == "bank_only"),
        }
    })


def parse_finanzblick_csv_from_string(text: str):
    """Parse Finanzblick CSV from a string. Reuses core logic via a temp StringIO."""
    import csv
    import re
    import tempfile
    import os
    from core.csv_parser import _extract_payee_from_csv_row
    from core.parsing import parse_amount

    transactions = []
    skipped = []

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
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


# ── Session: apply actions ────────────────────────────────────────────────────

@app.route("/api/session/<session_id>/apply", methods=["POST"])
def apply_actions(session_id: str):
    """
    Body (JSON):
    {
      "actions": [
        {"type": "clear",  "ynab_id": "..."},
        {"type": "update", "ynab_id": "...", "amount": -12.34},
        {"type": "create", "date": "2026-04-01", "amount": -12.34,
                           "payee_name": "...", "category_id": "...", "memo": "..."},
        {"type": "alias",  "bank_payee": "...", "ynab_payee": "..."},
        {"type": "defer",  "payee_name": "..."}
      ]
    }
    """
    try:
        sess = _session(session_id)
    except KeyError:
        return jsonify({"error": "Unbekannte Session – bitte neu starten"}), 404

    body = request.get_json(silent=True) or {}
    actions = body.get("actions", [])

    token      = sess["token"]
    budget_id  = sess["budget_id"]
    account_id = sess["account_id"]
    aliases    = sess["aliases"]
    config     = sess["config"]

    to_clear  = []
    to_update = []
    to_create = []
    deferred_added = []
    aliases_added  = []

    for action in actions:
        t = action.get("type")
        if t == "clear":
            to_clear.append(action["ynab_id"])
        elif t == "update":
            entry = {
                "id":      action["ynab_id"],
                "amount":  int(round(action["amount"] * 1000)),
                "cleared": "cleared"
            }
            if action.get("memo") is not None:
                entry["memo"] = action["memo"]
            if action.get("payee_name"):
                entry["payee_name"] = action["payee_name"]
            if action.get("category_id"):
                entry["category_id"] = action["category_id"]
            to_update.append(entry)
        elif t == "create":
            tx = {
                "account_id": account_id,
                "date":       action["date"],
                "amount":     int(round(action["amount"] * 1000)),
                "payee_name": action.get("payee_name", ""),
                "memo":       action.get("memo", ""),
                "cleared":    "cleared"
            }
            if action.get("category_id"):
                tx["category_id"] = action["category_id"]
            to_create.append(tx)
        elif t == "alias":
            save_alias(action["bank_payee"], action["ynab_payee"])
            aliases[action["bank_payee"].lower().strip()] = action["ynab_payee"]
            aliases_added.append(action["bank_payee"])
        elif t == "defer":
            keyword = add_deferred_payee(action["payee_name"], config)
            if keyword:
                deferred_added.append(keyword)

    results = {}
    try:
        if to_clear:
            payload = {"transactions": [{"id": tid, "cleared": "cleared"} for tid in to_clear]}
            api_patch(f"/budgets/{budget_id}/transactions", payload, token)
            results["cleared"] = len(to_clear)

        if to_update:
            api_patch(f"/budgets/{budget_id}/transactions", {"transactions": to_update}, token)
            results["updated"] = len(to_update)

        if to_create:
            api_post(f"/budgets/{budget_id}/transactions", {"transactions": to_create}, token)
            results["created"] = len(to_create)

    except APIError as e:
        return jsonify({"error": str(e)}), 502

    results["aliases_saved"]  = aliases_added
    results["deferred_saved"] = deferred_added

    # Clean up session
    del _sessions[session_id]

    return jsonify({"ok": True, "results": results})


# ── Session: reconcile (finalize) ─────────────────────────────────────────────

@app.route("/api/session/<session_id>/reconcile", methods=["POST"])
def reconcile(session_id: str):
    """
    Body: {"bank_balance": 1234.56}
    Marks cleared transactions as reconciled. Creates adjustment if balances differ.
    """
    try:
        sess = _session(session_id)
    except KeyError:
        return jsonify({"error": "Unbekannte Session – bitte neu starten"}), 404

    body        = request.get_json(silent=True) or {}
    bank_balance = body.get("bank_balance")
    if bank_balance is None:
        return jsonify({"error": "bank_balance erforderlich"}), 400

    token      = sess["token"]
    budget_id  = sess["budget_id"]
    account_id = sess["account_id"]

    try:
        account_data    = api_get(f"/budgets/{budget_id}/accounts/{account_id}", token)
        account         = account_data["data"]["account"]
        cleared_balance = account["cleared_balance"] / 1000.0

        diff = round(bank_balance - cleared_balance, 2)

        if abs(diff) >= 0.01:
            adj = {
                "account_id": account_id,
                "date":       datetime.now().strftime("%Y-%m-%d"),
                "amount":     int(round(diff * 1000)),
                "payee_name": "Ausgleichsbuchung",
                "memo":       "Reconciliation balance adjustment",
                "cleared":    "cleared"
            }
            api_post(f"/budgets/{budget_id}/transactions", {"transaction": adj}, token)

        txs_data    = api_get(f"/budgets/{budget_id}/accounts/{account_id}/transactions", token)
        cleared_txs = [t for t in txs_data["data"]["transactions"] if t.get("cleared") == "cleared"]
        if cleared_txs:
            payload = {"transactions": [{"id": t["id"], "cleared": "reconciled"} for t in cleared_txs]}
            api_patch(f"/budgets/{budget_id}/transactions", payload, token)

    except APIError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({
        "ok":             True,
        "adjustment":     round(diff, 2) if abs(diff) >= 0.01 else 0,
        "reconciled":     len(cleared_txs) if cleared_txs else 0,
        "cleared_balance": cleared_balance,
    })


# ── Payee & category search ───────────────────────────────────────────────────

@app.route("/api/session/<session_id>/payees")
def search_payees(session_id: str):
    try:
        sess = _session(session_id)
    except KeyError:
        return jsonify({"error": "Unbekannte Session"}), 404

    q = request.args.get("q", "").lower().strip()
    payees = sess["payees"]
    if q:
        matches = [p for p in payees if q in p["name"].lower()]
        if not matches:
            import difflib
            matches = sorted(
                payees,
                key=lambda p: difflib.SequenceMatcher(None, q, p["name"].lower()).ratio(),
                reverse=True
            )[:8]
        else:
            matches = matches[:10]
    else:
        matches = payees[:20]

    return jsonify({"payees": [{"id": p["id"], "name": p["name"]} for p in matches]})


@app.route("/api/session/<session_id>/categories")
def search_categories(session_id: str):
    try:
        sess = _session(session_id)
    except KeyError:
        return jsonify({"error": "Unbekannte Session"}), 404

    q = request.args.get("q", "").lower().strip()
    categories = sess["categories"]
    if q:
        matches = [c for c in categories if q in c["display"].lower()]
        if not matches:
            import difflib
            matches = sorted(
                categories,
                key=lambda c: difflib.SequenceMatcher(None, q, c["display"].lower()).ratio(),
                reverse=True
            )[:8]
        else:
            matches = matches[:10]
    else:
        matches = categories[:20]

    return jsonify({"categories": [{"id": c["id"], "display": c["display"]} for c in matches]})


# ── Account balance (for reconcile display) ───────────────────────────────────

@app.route("/api/session/<session_id>/balance")
def get_balance(session_id: str):
    try:
        sess = _session(session_id)
    except KeyError:
        return jsonify({"error": "Unbekannte Session"}), 404

    try:
        data    = api_get(f"/budgets/{sess['budget_id']}/accounts/{sess['account_id']}", sess["token"])
        account = data["data"]["account"]
    except APIError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({
        "cleared_balance": account["cleared_balance"] / 1000.0,
        "balance":         account["balance"] / 1000.0,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
