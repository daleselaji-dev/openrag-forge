"""集中式配置管理（12-Factor 实践教学）。

生产级配置管理的三条核心原则，本文件逐一示范：

1. **配置与代码分离**：所有可变项都来自环境变量（前缀 ``OPENRAG_``）或 ``.env``
   文件，代码里只有安全的开发默认值。同一个镜像可以不改一行代码地部署到
   dev / staging / production——只换配置。
2. **在哪里配置**（按部署方式）：
   - 本地开发：复制 ``.env.example`` 为 ``.env`` 后修改；
   - Docker Compose：``docker-compose.yml`` 的 ``environment:`` 块；
   - Kubernetes：非敏感项放 ``deploy/k8s/configmap.yaml``，
     敏感项（API key、数据库口令）放 Secret（见 ``deploy/k8s/secret.example.yaml``）。
3. **启动时校验，尽早失败**：``production_warnings()`` 会在启动时检查生产环境
   的危险默认值（CORS 全开、未启用认证等），并写入日志与 ``/api/v1/health``，
   让配置问题在部署时暴露，而不是在事故复盘时发现。

完整逐项说明见 ``docs/configuration.md``。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # 运行档位
    # ------------------------------------------------------------------
    profile: str = "lite"
    environment: str = "dev"
    host: str = "127.0.0.1"
    port: int = 18000
    graceful_shutdown_seconds: int = 30

    # ------------------------------------------------------------------
    # 真相源存储（truth source）
    # ------------------------------------------------------------------
    data_dir: Path = Path("./data")

    # ------------------------------------------------------------------
    # 派生检索索引 Qdrant（可重建，不是真相源）
    # ------------------------------------------------------------------
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "openrag_forge"
    qdrant_api_key: str = ""

    # ------------------------------------------------------------------
    # 模型端点（OpenAI 兼容协议：LM Studio / vLLM / llama.cpp / 云端）
    # ------------------------------------------------------------------
    chat_base_url: str = "http://localhost:1234/v1"
    embedding_base_url: str = "http://localhost:1234/v1"
    reranker_base_url: str = ""
    chat_model: str = "local-chat-model"
    embedding_model: str = "local-embedding-model"
    reranker_model: str = ""
    model_api_key: str = ""
    chat_api_key: str = ""
    embedding_api_key: str = ""
    reranker_api_key: str = ""

    # ------------------------------------------------------------------
    # 出站 HTTP 超时（每类调用单独可调）
    # ------------------------------------------------------------------
    http_timeout_seconds: float = 10.0
    probe_timeout_seconds: float = 2.0
    embedding_timeout_seconds: float = 60.0
    chat_timeout_seconds: float = 90.0
    index_timeout_seconds: float = 120.0

    # ------------------------------------------------------------------
    # 安全
    # ------------------------------------------------------------------
    cors_allow_origins: str = "*"
    api_key: str = ""
    rate_limit_per_minute: int = 0
    max_upload_mb: int = 64

    # ------------------------------------------------------------------
    # 可观测性（详见 docs/observability.md）
    # ------------------------------------------------------------------
    log_level: str = "info"
    log_format: str = "text"
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "openrag-forge"
    otel_sample_ratio: float = 1.0
    metrics_enabled: bool = True

    # Optional Langfuse OTLP exporter. Langfuse is an observability/evaluation
    # backend, not a node that changes the RAG answer path.
    langfuse_enabled: bool = False
    langfuse_base_url: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_sample_ratio: float = 1.0

    # ------------------------------------------------------------------
    # 检索与业务 Trace
    # ------------------------------------------------------------------
    retrieval_score_threshold: float = 0.5
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    def production_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.environment != "production":
            return warnings
        if "*" in self.cors_origin_list:
            warnings.append("CORS 允许所有来源（OPENRAG_CORS_ALLOW_ORIGINS=*）；生产环境应列出确切域名")
        if not self.api_key:
            warnings.append("未设置 OPENRAG_API_KEY，所有 /api/ 端点可匿名访问")
        if self.log_format != "json":
            warnings.append("OPENRAG_LOG_FORMAT 不是 json，日志平台将无法解析结构化字段")
        if not self.otel_enabled:
            warnings.append("OPENRAG_OTEL_ENABLED=false，无法在 Jaeger/Tempo 中查看分布式追踪")
        if self.profile == "lite":
            warnings.append("profile=lite 使用 SQLite 真相源，仅适合单副本；多副本请切换 production profile")
        if self.langfuse_enabled and not (self.langfuse_public_key and self.langfuse_secret_key):
            warnings.append("Langfuse 已开启但 public/secret key 不完整，无法写入 OTLP Trace")
        return warnings


settings = Settings()
