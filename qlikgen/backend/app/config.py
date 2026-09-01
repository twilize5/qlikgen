"""App-wide config: filesystem paths, keyring service name, service URLs."""
from pathlib import Path

APP_DIR = Path.home() / ".qlikgen"
APP_DIR.mkdir(exist_ok=True)

DB_PATH = APP_DIR / "qlikgen.db"

# Uploaded CSVs + their cached profiles live here between /profile and /generate.
UPLOADS_DIR = APP_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

KEYRING_SERVICE = "qlikgen"

# Fields in the keyring (secret).
KEYRING_KEYS = {
    "qlik_refresh_token": "qlik_refresh_token",
    "anthropic_api_key": "anthropic_api_key",
}

QIX_SIDECAR_URL = "http://127.0.0.1:8788"

# OAuth redirect URI must match what's registered in the Qlik OAuth client.
OAUTH_REDIRECT_URI = "http://localhost:8787/api/oauth/callback"

# Scopes:
#   admin_classic   — REST/Engine access
#   user_default    — basic user perms
#   offline_access  — issues a refresh_token so we don't force re-login each hour
OAUTH_SCOPES = "admin_classic user_default offline_access"
