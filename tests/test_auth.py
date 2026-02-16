"""Tests for the auth module."""

import json
import os
import stat
import platform
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from kroger_cart.auth import (
    FileStorage,
    TokenManager,
    generate_pkce,
    get_storage_backend,
)


class TestPKCE:
    """Test PKCE code generation."""

    def test_generates_verifier_and_challenge(self):
        verifier, challenge = generate_pkce()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 30
        assert len(challenge) > 30

    def test_different_each_time(self):
        v1, c1 = generate_pkce()
        v2, c2 = generate_pkce()
        assert v1 != v2
        assert c1 != c2

    def test_verifier_is_url_safe(self):
        verifier, _ = generate_pkce()
        # URL-safe base64 only contains these characters
        import re
        assert re.match(r'^[A-Za-z0-9_-]+$', verifier)


class TestFileStorage:
    """Test file-based token storage."""

    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "tokens.json")
        storage = FileStorage(path)
        tokens = {"access_token": "test123", "refresh_token": "ref456"}

        storage.save(tokens)
        loaded = storage.load()

        assert loaded["access_token"] == "test123"
        assert loaded["refresh_token"] == "ref456"

    def test_load_missing_file(self, tmp_path):
        storage = FileStorage(str(tmp_path / "nonexistent.json"))
        assert storage.load() is None

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix permissions only")
    def test_file_permissions_unix(self, tmp_path):
        path = str(tmp_path / "tokens.json")
        storage = FileStorage(path)
        storage.save({"access_token": "x"})

        file_stat = os.stat(path)
        mode = stat.S_IMODE(file_stat.st_mode)
        assert mode == 0o600


class TestStorageBackendDetection:
    """Test auto-detection of storage backend."""

    def test_force_file(self, tmp_path):
        backend = get_storage_backend(str(tmp_path / "t.json"), force="file")
        assert isinstance(backend, FileStorage)

    def test_no_keyring_falls_back_to_file(self, tmp_path):
        """Without keyring installed, should fall back to file."""
        with patch.dict("sys.modules", {"keyring": None}):
            backend = get_storage_backend(str(tmp_path / "t.json"))
            assert isinstance(backend, FileStorage)


class TestTokenManager:
    """Test token management logic."""

    def _make_manager(self, tmp_path, tokens=None):
        config = {
            "client_id": "test-id",
            "client_secret": "test-secret",
            "redirect_uri": "http://localhost:3000",
            "auth_url": "https://api.kroger.com/v1/connect/oauth2/authorize",
            "token_url": "https://api.kroger.com/v1/connect/oauth2/token",
            "token_file": str(tmp_path / "tokens.json"),
        }
        session = MagicMock()
        storage = FileStorage(config["token_file"])
        if tokens:
            storage.save(tokens)
        return TokenManager(config, session, storage)

    def test_returns_cached_token_if_valid(self, tmp_path):
        tokens = {
            "access_token": "cached-token",
            "refresh_token": "ref",
            "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
        }
        mgr = self._make_manager(tmp_path, tokens)
        assert mgr.get_access_token() == "cached-token"

    def test_expired_token_triggers_refresh(self, tmp_path):
        tokens = {
            "access_token": "old",
            "refresh_token": "ref",
            "expires_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        }
        mgr = self._make_manager(tmp_path, tokens)

        # Mock the refresh endpoint
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "access_token": "new-token",
            "refresh_token": "new-ref",
            "expires_in": 1800,
        }
        mock_resp.raise_for_status = MagicMock()
        mgr.session.post.return_value = mock_resp

        result = mgr.get_access_token()
        assert result == "new-token"
        mgr.session.post.assert_called_once()
