"""CLI entry: `uv run python -m cc_i18n_proxy`."""
import uvicorn
from cc_i18n_proxy.config import Config


def main() -> None:
    cfg = Config.from_env()
    uvicorn.run(
        "cc_i18n_proxy.server:app",
        host="127.0.0.1",
        port=cfg.listen_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
