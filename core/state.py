import difflib
import json
import os
from pathlib import Path


def _data_dir() -> Path:
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).parent.parent  # project root by default


def _aliases_file() -> Path:
    return _data_dir() / "aliases.json"


def _config_file() -> Path:
    return _data_dir() / "config.json"


# ── Aliases ───────────────────────────────────────────────────────────────────

def load_aliases() -> dict:
    f = _aliases_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_alias(bank_payee: str, ynab_payee: str) -> None:
    aliases = load_aliases()
    aliases[bank_payee.lower().strip()] = ynab_payee.strip()
    _aliases_file().write_text(
        json.dumps(aliases, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def resolve_alias(bank_payee: str, aliases: dict) -> str:
    key = bank_payee.lower().strip()
    if key in aliases:
        return aliases[key]
    for alias_key, ynab_name in aliases.items():
        if alias_key in key or key in alias_key:
            return ynab_name
    return bank_payee


# ── Config / deferred payees ──────────────────────────────────────────────────

def load_config() -> dict:
    defaults = {"deferred_payees": []}
    f = _config_file()
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            defaults.update(data)
        except Exception:
            pass
    return defaults


def save_config(config: dict) -> None:
    _config_file().write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def is_deferred_payee(payee_name: str, config: dict) -> bool:
    if not payee_name:
        return False
    lower = payee_name.lower()
    return any(kw in lower for kw in config.get("deferred_payees", []))


def add_deferred_payee(payee_name: str, config: dict) -> str:
    """Adds payee to deferred list and saves. Returns the keyword added, or '' if already present."""
    words = payee_name.lower().split()
    keyword = next((w for w in words if len(w) >= 4), payee_name.lower()[:10])
    if keyword not in config["deferred_payees"]:
        config["deferred_payees"].append(keyword)
        save_config(config)
        return keyword
    return ""
