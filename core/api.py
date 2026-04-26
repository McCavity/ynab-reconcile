import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://api.ynab.com/v1"


class TokenNotFoundError(Exception):
    pass


class APIError(Exception):
    def __init__(self, message: str, status_code: int = None):
        super().__init__(message)
        self.status_code = status_code


def load_token() -> str:
    token = os.environ.get("YNAB_API_TOKEN", "")
    if token and token != "dein_token_hier_eintragen":
        return token
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("YNAB_API_TOKEN=") and not line.startswith("#"):
                token = line.split("=", 1)[1].strip()
                if token and token != "dein_token_hier_eintragen":
                    return token
    raise TokenNotFoundError("Kein API-Token gefunden! Bitte .env-Datei anlegen.")


def api_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise APIError(f"HTTP {e.code}: {e.read().decode()}", status_code=e.code)
    except urllib.error.URLError as e:
        raise APIError(f"Verbindungsfehler: {e.reason}")


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
        raise APIError(f"HTTP {e.code}: {e.read().decode()}", status_code=e.code)
    except urllib.error.URLError as e:
        raise APIError(f"Verbindungsfehler: {e.reason}")


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
        raise APIError(f"HTTP {e.code}: {e.read().decode()}", status_code=e.code)
    except urllib.error.URLError as e:
        raise APIError(f"Verbindungsfehler: {e.reason}")
