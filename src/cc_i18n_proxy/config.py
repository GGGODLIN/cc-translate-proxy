"""Environment-based configuration."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    listen_port: int
    anthropic_upstream: str
    home: Path
    cache_db_path: Path
    audit_log_dir: Path
    emit_file_dir: Path
    log_protocol_observations: bool
    protocol_observations_path: Path

    @classmethod
    def from_env(cls) -> "Config":
        home = Path(os.environ.get("CC_I18N_PROXY_HOME", Path.home() / ".cc-i18n-proxy"))
        home.mkdir(parents=True, exist_ok=True)
        (home / "audit").mkdir(exist_ok=True)
        return cls(
            listen_port=int(os.environ.get("CC_I18N_PROXY_PORT", "8080")),
            anthropic_upstream=os.environ.get("ANTHROPIC_UPSTREAM", "https://api.anthropic.com"),
            home=home,
            cache_db_path=home / "cache.db",
            audit_log_dir=home / "audit",
            emit_file_dir=Path(os.environ.get("CC_I18N_PROXY_EMIT_DIR", "/tmp")),
            log_protocol_observations=os.environ.get("CC_I18N_PROXY_LOG_PROTOCOL", "0") == "1",
            protocol_observations_path=Path(__file__).resolve().parents[2] / "docs" / "protocol-observations.md",
        )
