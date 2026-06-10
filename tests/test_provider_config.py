"""Tests for providers.toml parsing, .env loading, chain factory."""
import os
from pathlib import Path

import pytest

from cc_i18n_proxy.providers.config import (
    build_chain_from_config,
    load_providers_config,
)
from cc_i18n_proxy.translator import (
    GeminiNativeAdapter,
    OpenAICompatAdapter,
)


@pytest.fixture
def env_keys():
    """Snapshot/restore environ around tests."""
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_minimal_toml(tmp_path, env_keys):
    os.environ["TEST_KEY"] = "secret"
    toml_path = _write(tmp_path / "providers.toml", '''
default_chain = ["only"]

[providers.only]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "TEST_KEY"
model = "test-model"
display_name = "Only Provider"
''')
    cfg = load_providers_config(toml_path)
    assert cfg.default_chain == ["only"]
    assert "only" in cfg.providers
    assert cfg.providers["only"].kind == "openai-compat"
    assert cfg.providers["only"].model == "test-model"


def test_default_enabled_is_true(tmp_path, env_keys):
    os.environ["K"] = "v"
    toml_path = _write(tmp_path / "providers.toml", '''
default_chain = ["x"]
[providers.x]
kind = "openai-compat"
base_url = "u"
api_key_env = "K"
model = "m"
display_name = "X"
''')
    cfg = load_providers_config(toml_path)
    assert cfg.providers["x"].enabled is True


def test_disabled_provider_excluded_from_chain(tmp_path, env_keys):
    os.environ["K"] = "v"
    toml_path = _write(tmp_path / "providers.toml", '''
default_chain = ["x", "y"]
[providers.x]
kind = "openai-compat"
base_url = "u"
api_key_env = "K"
model = "m"
display_name = "X"
[providers.y]
kind = "openai-compat"
base_url = "u"
api_key_env = "K"
model = "m"
display_name = "Y"
enabled = false
''')
    cfg = load_providers_config(toml_path)
    chain = build_chain_from_config(cfg, active_head_reader=lambda: None)
    names = chain.default_chain_names()
    assert "x" in names and "y" not in names
    assert "y" not in chain.enabled_adapters()


def test_missing_api_key_disables_provider(tmp_path, env_keys, caplog):
    os.environ.pop("MISSING_KEY", None)
    os.environ["EXISTING"] = "v"
    toml_path = _write(tmp_path / "providers.toml", '''
default_chain = ["a", "b"]
[providers.a]
kind = "openai-compat"
base_url = "u"
api_key_env = "MISSING_KEY"
model = "m"
display_name = "A"
[providers.b]
kind = "openai-compat"
base_url = "u"
api_key_env = "EXISTING"
model = "m"
display_name = "B"
''')
    cfg = load_providers_config(toml_path)
    chain = build_chain_from_config(cfg, active_head_reader=lambda: None)
    assert chain.default_chain_names() == ["b"]
    assert "a" not in chain.enabled_adapters()


def test_empty_default_chain_after_filtering_raises(tmp_path, env_keys):
    os.environ.pop("MISSING_KEY", None)
    toml_path = _write(tmp_path / "providers.toml", '''
default_chain = ["a"]
[providers.a]
kind = "openai-compat"
base_url = "u"
api_key_env = "MISSING_KEY"
model = "m"
display_name = "A"
''')
    cfg = load_providers_config(toml_path)
    with pytest.raises(ValueError, match="empty"):
        build_chain_from_config(cfg, active_head_reader=lambda: None)


def test_missing_toml_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_providers_config(tmp_path / "nonexistent.toml")


def test_broken_toml_raises_with_context(tmp_path):
    toml_path = _write(tmp_path / "providers.toml", "this is not valid TOML {{{")
    with pytest.raises(ValueError, match="parse"):
        load_providers_config(toml_path)


def test_missing_required_field_raises(tmp_path, env_keys):
    os.environ["K"] = "v"
    toml_path = _write(tmp_path / "providers.toml", '''
default_chain = ["x"]
[providers.x]
kind = "openai-compat"
api_key_env = "K"
display_name = "X"
''')
    with pytest.raises(ValueError, match="model"):
        load_providers_config(toml_path)


def test_unknown_kind_raises(tmp_path, env_keys):
    os.environ["K"] = "v"
    toml_path = _write(tmp_path / "providers.toml", '''
default_chain = ["x"]
[providers.x]
kind = "unknown-kind"
base_url = "u"
api_key_env = "K"
model = "m"
display_name = "X"
''')
    with pytest.raises(ValueError, match="kind"):
        load_providers_config(toml_path)


def test_chain_factory_creates_adapter_types(tmp_path, env_keys):
    os.environ["GEMINI_API_KEY"] = "g"
    os.environ["GROQ_API_KEY"] = "k"
    toml_path = _write(tmp_path / "providers.toml", '''
default_chain = ["groq-llama", "gem"]
[providers.groq-llama]
kind = "openai-compat"
base_url = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"
model = "llama-3.3-70b-versatile"
display_name = "Groq · Llama"
[providers.gem]
kind = "gemini-native"
api_key_env = "GEMINI_API_KEY"
model = "gemma-3-12b-it"
display_name = "Gemini · Gemma"
''')
    cfg = load_providers_config(toml_path)
    chain = build_chain_from_config(cfg, active_head_reader=lambda: None)
    enabled = chain.enabled_adapters()
    assert isinstance(enabled["groq-llama"].adapter, OpenAICompatAdapter)
    assert isinstance(enabled["gem"].adapter, GeminiNativeAdapter)


def test_dotenv_load_from_path(tmp_path, env_keys):
    """When .env path provided, values populate os.environ if not already set."""
    env_path = tmp_path / ".env"
    env_path.write_text('export TEST_LOAD_KEY="abc123"\n')
    os.environ.pop("TEST_LOAD_KEY", None)
    cfg_path = _write(tmp_path / "providers.toml", '''
default_chain = ["x"]
[providers.x]
kind = "openai-compat"
base_url = "u"
api_key_env = "TEST_LOAD_KEY"
model = "m"
display_name = "X"
''')
    load_providers_config(cfg_path, dotenv_path=env_path)
    assert os.environ.get("TEST_LOAD_KEY") == "abc123"


def test_existing_env_wins_over_dotenv(tmp_path, env_keys):
    env_path = tmp_path / ".env"
    env_path.write_text("WINS=from_dotenv\n")
    os.environ["WINS"] = "from_env"
    cfg_path = _write(tmp_path / "providers.toml", '''
default_chain = ["x"]
[providers.x]
kind = "openai-compat"
base_url = "u"
api_key_env = "WINS"
model = "m"
display_name = "X"
''')
    load_providers_config(cfg_path, dotenv_path=env_path)
    assert os.environ["WINS"] == "from_env"


def test_gemini_native_empty_key_skips_provider(tmp_path, env_keys):
    """gemini-native rejects empty api_key with ValueError; chain factory should skip + warn."""
    os.environ.pop("ABSENT", None)
    os.environ["GROQ"] = "ok"
    cfg_path = _write(tmp_path / "providers.toml", '''
default_chain = ["g", "groq"]
[providers.g]
kind = "gemini-native"
api_key_env = "ABSENT"
model = "m"
display_name = "G"
[providers.groq]
kind = "openai-compat"
base_url = "u"
api_key_env = "GROQ"
model = "m"
display_name = "Groq"
''')
    cfg = load_providers_config(cfg_path)
    chain = build_chain_from_config(cfg, active_head_reader=lambda: None)
    assert "g" not in chain.enabled_adapters()
    assert chain.default_chain_names() == ["groq"]


def test_empty_default_chain_raises_at_parse(tmp_path):
    cfg_path = _write(tmp_path / "providers.toml", "default_chain = []\n")
    with pytest.raises(ValueError, match="default_chain must not be empty"):
        load_providers_config(cfg_path)
