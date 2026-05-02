"""Shared pytest fixtures."""
from pathlib import Path

import pytest

from cc_i18n_proxy.config import Config


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(tmp_path / "proxy_home"))
    monkeypatch.setenv("CC_I18N_PROXY_EMIT_DIR", str(tmp_path / "emit"))
    (tmp_path / "emit").mkdir(parents=True, exist_ok=True)
    return Config.from_env()
