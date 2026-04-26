from .api import api_get


def load_ynab_payees(budget_id: str, token: str) -> list:
    """Lädt alle Payees aus YNAB (ohne gelöschte)."""
    data = api_get(f"/budgets/{budget_id}/payees", token)
    return [p for p in data["data"]["payees"] if not p.get("deleted")]


def load_ynab_categories(budget_id: str, token: str) -> list:
    """
    Lädt alle Kategorien aus YNAB als flache Liste.
    Gibt eine Liste von Dicts: {id, name, group_name, display} zurück.
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
