from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    profile: str = "lite"
    service_name: str = "openrag-forge-api"
    service_env: str = "dev"
    log_level: str = "INFO"
    enable_request_log: bool = True
    trace_sample_rate: float = 1.0
    cors_allow_origins: str = "*"
    data_dir: Path = Path("./data")
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "openrag_forge"
    chat_base_url: str = "http://localhost:1234/v1"
    embedding_base_url: str = "http://localhost:1234/v1"
    reranker_base_url: str = ""
    chat_model: str = "local-chat-model"
    embedding_model: str = "local-embedding-model"
    reranker_model: str = ""
    max_upload_mb: int = 64
    retrieval_score_threshold: float = 0.5
    default_top_k: int = 5
    chunk_max_chars: int = 1200
    chunk_overlap: int = 120
    trace_persistence: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_prefix="OPENRAG_", extra="ignore")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "openrag.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "artifacts"


settings = Settings()
