"""Unit tests for proxy settings."""

import json
from pathlib import Path

import pytest

from webctl.config import WebctlConfig, resolve_proxy_settings


class TestResolveProxySettings:
    """Tests for resolve_proxy_settings function."""

    def test_no_proxy_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no proxy is configured, returns None."""
        # Clear all proxy-related env vars
        for var in [
            "WEBCTL_PROXY_SERVER",
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "NO_PROXY",
            "no_proxy",
        ]:
            monkeypatch.delenv(var, raising=False)

        # Use temp config dir with no proxy settings
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is None

    def test_webctl_proxy_server_highest_priority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """WEBCTL_PROXY_SERVER takes precedence over all other sources."""
        monkeypatch.setenv("WEBCTL_PROXY_SERVER", "http://webctl-proxy:8080")
        monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy:8080")
        monkeypatch.setenv("HTTP_PROXY", "http://http-proxy:8080")

        # Use temp config with different proxy
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"proxy_server": "http://config-proxy:8080"}))
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["server"] == "http://webctl-proxy:8080"

    def test_https_proxy_before_http_proxy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTPS_PROXY takes precedence over HTTP_PROXY."""
        monkeypatch.delenv("WEBCTL_PROXY_SERVER", raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy:8080")
        monkeypatch.setenv("HTTP_PROXY", "http://http-proxy:8080")

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["server"] == "http://https-proxy:8080"

    def test_http_proxy_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP_PROXY is used when higher priority sources are not set."""
        for var in ["WEBCTL_PROXY_SERVER", "HTTPS_PROXY", "https_proxy"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTP_PROXY", "http://http-proxy:8080")

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["server"] == "http://http-proxy:8080"

    def test_lowercase_env_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lowercase env vars (https_proxy, http_proxy) are also recognized."""
        for var in ["WEBCTL_PROXY_SERVER", "HTTPS_PROXY", "HTTP_PROXY"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("https_proxy", "http://lowercase-proxy:8080")

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["server"] == "http://lowercase-proxy:8080"

    def test_config_file_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config file proxy_server is used when no env vars are set."""
        for var in [
            "WEBCTL_PROXY_SERVER",
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
        ]:
            monkeypatch.delenv(var, raising=False)

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"proxy_server": "http://config-proxy:8080"}))
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["server"] == "http://config-proxy:8080"

    def test_authenticated_proxy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proxy with username and password from config."""
        for var in [
            "WEBCTL_PROXY_SERVER",
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "NO_PROXY",
            "no_proxy",
        ]:
            monkeypatch.delenv(var, raising=False)

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "proxy_server": "http://auth-proxy:8080",
                    "proxy_username": "testuser",
                    "proxy_password": "testpass",
                }
            )
        )
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["server"] == "http://auth-proxy:8080"
        assert result["username"] == "testuser"
        assert result["password"] == "testpass"

    def test_no_proxy_bypass_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NO_PROXY env var adds bypass list."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
        monkeypatch.setenv("NO_PROXY", "localhost,*.internal.com")

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["server"] == "http://proxy:8080"
        assert result["bypass"] == "localhost,*.internal.com"

    def test_proxy_bypass_from_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """proxy_bypass from config is used when NO_PROXY env var not set."""
        for var in ["NO_PROXY", "no_proxy"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"proxy_bypass": "localhost,127.0.0.1"}))
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["bypass"] == "localhost,127.0.0.1"

    def test_env_no_proxy_overrides_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NO_PROXY env var takes precedence over config proxy_bypass."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
        monkeypatch.setenv("NO_PROXY", "env-bypass.com")

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"proxy_bypass": "config-bypass.com"}))
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        result = resolve_proxy_settings()
        assert result is not None
        assert result["bypass"] == "env-bypass.com"


class TestWebctlConfigProxyFields:
    """Tests for proxy fields in WebctlConfig."""

    def test_default_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proxy fields default to None."""
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        config = WebctlConfig.load()
        assert config.proxy_server is None
        assert config.proxy_username is None
        assert config.proxy_password is None
        assert config.proxy_bypass is None

    def test_load_proxy_from_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proxy fields are loaded from config file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "proxy_server": "http://proxy:8080",
                    "proxy_username": "user",
                    "proxy_password": "pass",
                    "proxy_bypass": "localhost",
                }
            )
        )
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        config = WebctlConfig.load()
        assert config.proxy_server == "http://proxy:8080"
        assert config.proxy_username == "user"
        assert config.proxy_password == "pass"
        assert config.proxy_bypass == "localhost"

    def test_save_proxy_to_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proxy fields are saved to config file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        monkeypatch.setattr("webctl.config.get_config_dir", lambda: config_dir)

        config = WebctlConfig()
        config.proxy_server = "http://saved-proxy:8080"
        config.proxy_username = "saveduser"
        config.proxy_password = "savedpass"
        config.proxy_bypass = "*.internal"
        config.save()

        # Reload and verify
        config_file = config_dir / "config.json"
        data = json.loads(config_file.read_text())
        assert data["proxy_server"] == "http://saved-proxy:8080"
        assert data["proxy_username"] == "saveduser"
        assert data["proxy_password"] == "savedpass"
        assert data["proxy_bypass"] == "*.internal"
