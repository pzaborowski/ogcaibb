from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HubSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = Field(default="127.0.0.1", alias="OGCAIBB_HUB_HOST")
    port: int = Field(default=8080, alias="OGCAIBB_HUB_PORT")
    log_level: str = Field(default="INFO", alias="OGCAIBB_HUB_LOG_LEVEL")

    # --- Auth -----------------------------------------------------------
    auth: str = Field(default="apikey", alias="OGCAIBB_HUB_AUTH")
    apikeys_file: Path = Field(
        default=Path("/etc/ogcaibb-hub/keys.yaml"),
        alias="OGCAIBB_HUB_APIKEYS_FILE",
    )

    # --- Storage --------------------------------------------------------
    trace_store: str = Field(default="localfs", alias="OGCAIBB_HUB_TRACE_STORE")
    trace_store_path: Path = Field(
        default=Path("/var/lib/ogcaibb-hub/chunks"),
        alias="OGCAIBB_HUB_TRACE_STORE_PATH",
    )
    index_store: str = Field(default="sqlite", alias="OGCAIBB_HUB_INDEX_STORE")
    index_store_path: Path = Field(
        default=Path("/var/lib/ogcaibb-hub/state.db"),
        alias="OGCAIBB_HUB_INDEX_STORE_PATH",
    )

    # --- Retrieval (embedder + vector store) ---------------------------
    embedder: str | None = Field(default=None, alias="OGCAIBB_HUB_EMBEDDER")
    embedder_host: str = Field(
        default="http://localhost:11434", alias="OGCAIBB_HUB_EMBEDDER_HOST"
    )
    embedder_model: str = Field(
        default="nomic-embed-text", alias="OGCAIBB_HUB_EMBEDDER_MODEL"
    )
    embedder_api_key: str | None = Field(
        default=None, alias="OGCAIBB_HUB_EMBEDDER_API_KEY"
    )

    vector_store: str | None = Field(default=None, alias="OGCAIBB_HUB_VECTOR_STORE")
    vector_store_url: str = Field(
        default="http://localhost:6333", alias="OGCAIBB_HUB_VECTOR_STORE_URL"
    )
    vector_store_collection: str = Field(
        default="ogcaibb-traces", alias="OGCAIBB_HUB_VECTOR_STORE_COLLECTION"
    )
    vector_store_api_key: str | None = Field(
        default=None, alias="OGCAIBB_HUB_VECTOR_STORE_API_KEY"
    )

    # --- Limits ---------------------------------------------------------
    max_chunk_bytes: int = Field(
        default=16 * 1024 * 1024, alias="OGCAIBB_HUB_MAX_CHUNK_BYTES"
    )
    accepted_schema_versions: tuple[int, ...] = (1,)


settings = HubSettings()
