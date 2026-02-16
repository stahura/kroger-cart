"""
OAuth2 + PKCE authentication for the Kroger API.
Handles token storage with optional OS keyring support and file-based fallback.
"""

import os
import json
import stat
import base64
import hashlib
import secrets
import platform
import logging
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, urlencode
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ─── Token Storage Backends ─────────────────────────────────────────────────


class FileStorage:
    """Store tokens in a local JSON file with restricted permissions."""

    def __init__(self, path: str):
        self.path = path

    def save(self, tokens: dict) -> None:
        with open(self.path, "w") as f:
            json.dump(tokens, f)
        # Restrict file permissions on Unix (Windows is single-user by default)
        if platform.system() != "Windows":
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)  # 600
        logger.debug(f"Tokens saved to {self.path} (file storage)")

    def load(self) -> dict | None:
        if not os.path.exists(self.path):
            return None
        with open(self.path, "r") as f:
            return json.load(f)

    def __str__(self):
        return f"File ({self.path})"


class KeyringStorage:
    """Store tokens in the OS keychain via the keyring library."""

    SERVICE_NAME = "kroger-cart"
    KEY_NAME = "oauth-tokens"

    def save(self, tokens: dict) -> None:
        import keyring

        keyring.set_password(self.SERVICE_NAME, self.KEY_NAME, json.dumps(tokens))
        logger.debug("Tokens saved to OS keychain (keyring storage)")

    def load(self) -> dict | None:
        import keyring

        data = keyring.get_password(self.SERVICE_NAME, self.KEY_NAME)
        return json.loads(data) if data else None

    def __str__(self):
        return "OS Keychain"


def get_storage_backend(token_file: str, force: str | None = None):
    """Auto-detect the best token storage backend.

    Args:
        token_file: Path to fallback token file.
        force: Force a specific backend ('file' or 'keyring').

    Returns:
        A storage backend instance (FileStorage or KeyringStorage).
    """
    if force == "file":
        return FileStorage(token_file)

    if force == "keyring":
        try:
            import keyring

            keyring.get_keyring()
            return KeyringStorage()
        except Exception as e:
            logger.warning(f"Keyring requested but unavailable: {e}. Falling back to file.")
            return FileStorage(token_file)

    # Auto-detect: try keyring, fall back to file
    try:
        import keyring

        backend = keyring.get_keyring()
        backend_name = type(backend).__name__.lower()
        if "fail" in backend_name or "null" in backend_name:
            raise RuntimeError("No usable keyring backend")
        return KeyringStorage()
    except Exception:
        return FileStorage(token_file)


# ─── OAuth Callback Server ──────────────────────────────────────────────────


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback from Kroger."""

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if "code" in query:
            self.server.auth_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body>"
                b"<h1>Authentication Successful!</h1>"
                b"<p>You can close this window and return to the terminal.</p>"
                b"</body></html>"
            )
        elif "error" in query:
            self.server.auth_error = query["error"][0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            error_msg = query["error"][0]
            self.wfile.write(
                f"<html><body>"
                f"<h1>Authentication Failed</h1>"
                f"<p>Error: {error_msg}</p>"
                f"</body></html>".encode()
            )
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default HTTP server logs."""
        pass


# ─── PKCE ────────────────────────────────────────────────────────────────────


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge.

    Returns:
        Tuple of (code_verifier, code_challenge).
    """
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return code_verifier, code_challenge


# ─── Token Management ────────────────────────────────────────────────────────


class TokenManager:
    """Manages OAuth tokens: load, save, refresh, and authenticate."""

    def __init__(self, config: dict, session, storage=None):
        """
        Args:
            config: Dict with client_id, client_secret, redirect_uri, auth_url, token_url.
            session: requests.Session for HTTP calls.
            storage: Token storage backend (auto-detected if None).
        """
        self.client_id = config["client_id"]
        self.client_secret = config.get("client_secret", "")
        self.redirect_uri = config["redirect_uri"]
        self.auth_url = config["auth_url"]
        self.token_url = config["token_url"]
        self.scopes = config.get("scopes", "product.compact cart.basic:write profile.compact")
        self.session = session
        self.storage = storage or get_storage_backend(
            config.get("token_file", "tokens.json")
        )
        logger.info(f"Token storage: {self.storage}")

    def get_access_token(self) -> str:
        """Get a valid access token, refreshing or re-authenticating if needed."""
        tokens = self.storage.load()

        if tokens and not self._is_expired(tokens):
            logger.debug("Using cached access token")
            return tokens["access_token"]

        if tokens and "refresh_token" in tokens:
            try:
                return self._refresh(tokens["refresh_token"])
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")
                logger.info("Starting new OAuth flow...")

        return self._authenticate()

    def _is_expired(self, tokens: dict) -> bool:
        """Check if the access token is expired (with 5-minute buffer)."""
        if "expires_at" not in tokens:
            return True
        expires = datetime.fromisoformat(tokens["expires_at"])
        return datetime.now() >= expires - timedelta(minutes=5)

    def _refresh(self, refresh_token: str) -> str:
        """Refresh the access token using a refresh token."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret

        response = self.session.post(self.token_url, data=data)
        response.raise_for_status()
        tokens = response.json()
        self._save(tokens)
        logger.info("Access token refreshed successfully")
        return tokens["access_token"]

    def _authenticate(self) -> str:
        """Run the full OAuth2 + PKCE browser flow."""
        if not self.client_id:
            raise ValueError(
                "KROGER_CLIENT_ID not set. Copy .env.example to .env and fill in your credentials."
            )

        code_verifier, code_challenge = generate_pkce()
        state = secrets.token_urlsafe(16)

        auth_params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": self.scopes,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{self.auth_url}?{urlencode(auth_params)}"

        logger.info("Opening browser for authentication...")
        logger.info(f"If browser doesn't open, visit:\n{auth_url}\n")
        webbrowser.open(auth_url)

        # Start local callback server
        class ReusableTCPServer(HTTPServer):
            allow_reuse_address = True

        server = ReusableTCPServer(("localhost", 3000), OAuthCallbackHandler)
        server.auth_code = None
        server.auth_error = None

        logger.info("Waiting for authentication...")
        while server.auth_code is None and server.auth_error is None:
            server.handle_request()

        if server.auth_error:
            raise Exception(f"Authentication error: {server.auth_error}")

        # Exchange code for tokens
        token_data = {
            "grant_type": "authorization_code",
            "code": server.auth_code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        if self.client_secret:
            token_data["client_secret"] = self.client_secret

        response = self.session.post(self.token_url, data=token_data)
        response.raise_for_status()
        tokens = response.json()
        self._save(tokens)
        logger.info("Authentication successful!")

        return tokens["access_token"]

    def _save(self, tokens: dict) -> None:
        """Save tokens with expiration timestamp."""
        tokens["expires_at"] = (
            datetime.now() + timedelta(seconds=tokens.get("expires_in", 1800))
        ).isoformat()
        self.storage.save(tokens)
