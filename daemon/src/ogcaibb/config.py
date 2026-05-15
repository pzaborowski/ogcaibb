from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    ollama_api_key: str | None = Field(default=None, alias="OLLAMA_API_KEY")

    model_chat: str = Field(default="qwen2.5-coder:32b-instruct", alias="OGCAIBB_MODEL_CHAT")
    model_router: str = Field(default="qwen2.5:7b-instruct", alias="OGCAIBB_MODEL_ROUTER")
    model_embed: str = Field(default="nomic-embed-text", alias="OGCAIBB_MODEL_EMBED")

    host: str = Field(default="127.0.0.1", alias="OGCAIBB_HOST")
    port: int = Field(default=4141, alias="OGCAIBB_PORT")
    # Explicit base URL used by the CLI to talk back to a running daemon.
    # When unset, the CLI derives it from host/port (with 0.0.0.0 → 127.0.0.1).
    daemon_url: str | None = Field(default=None, alias="OGCAIBB_DAEMON_URL")
    workspace_root: Path = Field(default=Path.cwd(), alias="OGCAIBB_WORKSPACE_ROOT")
    log_level: str = Field(default="INFO", alias="OGCAIBB_LOG_LEVEL")

    memory_dir: Path = Field(
        default=Path.home() / ".local/share/ogcaibb/memory",
        alias="OGCAIBB_MEMORY_DIR",
    )
    vector_backend: str = Field(default="chroma", alias="OGCAIBB_VECTOR_BACKEND")
    vector_path: Path = Field(
        default=Path.home() / ".local/share/ogcaibb/chroma",
        alias="OGCAIBB_VECTOR_PATH",
    )

    # Where to find skill/agent/command manifests. Defaults to the repo root.
    manifests_root: Path = Field(
        default=Path(__file__).resolve().parents[3],
        alias="OGCAIBB_MANIFESTS_ROOT",
    )

    # --- Trace capture pipeline ---------------------------------------
    trace_enabled: bool = Field(default=True, alias="OGCAIBB_TRACE_ENABLED")
    trace_dir: Path = Field(
        default=Path.home() / ".local/share/ogcaibb/traces",
        alias="OGCAIBB_TRACE_DIR",
    )
    trace_max_bytes: int = Field(default=4 * 1024 * 1024, alias="OGCAIBB_TRACE_MAX_BYTES")
    trace_max_age_seconds: float = Field(default=300.0, alias="OGCAIBB_TRACE_MAX_AGE_SECONDS")
    trace_max_total_bytes: int = Field(
        default=200 * 1024 * 1024, alias="OGCAIBB_TRACE_MAX_TOTAL_BYTES"
    )
    trace_transport: str = Field(default="noop", alias="OGCAIBB_TRACE_TRANSPORT")
    redactor: str = Field(default="ruleset", alias="OGCAIBB_REDACTOR")
    redactor_rules: Path | None = Field(default=None, alias="OGCAIBB_REDACTOR_RULES")
    workstation_id_path: Path = Field(
        default=Path.home() / ".local/share/ogcaibb/workstation_id",
        alias="OGCAIBB_WORKSTATION_ID_PATH",
    )

    # Comma-separated detector names. Empty string = disabled.
    # Defaults to the cheap baseline; opt in to IO-heavy detectors per deployment.
    implicit_signals: str = Field(
        default="tool_call_completed_clean",
        alias="OGCAIBB_IMPLICIT_SIGNALS",
    )
    implicit_edit_retention_delay: float = Field(
        default=600.0, alias="OGCAIBB_IMPLICIT_EDIT_RETENTION_DELAY"
    )
    implicit_git_commit_delay: float = Field(
        default=1800.0, alias="OGCAIBB_IMPLICIT_GIT_COMMIT_DELAY"
    )

    # --- Hub client ---------------------------------------------------
    hub_url: str | None = Field(default=None, alias="OGCAIBB_HUB_URL")
    hub_auth: str = Field(default="apikey", alias="OGCAIBB_HUB_AUTH")
    hub_token: str | None = Field(default=None, alias="OGCAIBB_HUB_TOKEN")

    @property
    def num_ctx(self) -> int:
        return 32768

    def resolve(self, p: Path) -> Path:
        return p.expanduser().resolve()


settings = Settings()
