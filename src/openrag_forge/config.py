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
    #: 能力档位：lite（SQLite + 本地文件）或 production / observability / graph 等。
    #: 决定“装了哪些组件”，与 environment（部署到哪一环境）是两个维度。
    profile: str = "lite"

    #: 部署环境：dev | staging | production。
    #: 设为 production 时 production_warnings() 会启用严格检查。
    environment: str = "dev"

    #: 服务监听地址与端口。容器内用 0.0.0.0，本机开发用 127.0.0.1（不对外暴露）。
    host: str = "127.0.0.1"
    port: int = 18000

    #: 优雅关机窗口（秒）：收到 SIGTERM 后允许在途请求完成的时间。
    #: 必须小于编排层的强杀超时（K8s terminationGracePeriodSeconds，见 deploy/k8s/deployment.yaml）。
    graceful_shutdown_seconds: int = 30

    # ------------------------------------------------------------------
    # 真相源存储（truth source）
    # ------------------------------------------------------------------
    #: SQLite 数据库、上传原件与 Evidence Capsule 的根目录。
    #: 生产容器里应挂载持久卷（compose volume / K8s PVC），否则重启即丢数据。
    data_dir: Path = Path("./data")

    # ------------------------------------------------------------------
    # 派生检索索引 Qdrant（可重建，不是真相源）
    # ------------------------------------------------------------------
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "openrag_forge"
    #: Qdrant Cloud 或自建鉴权集群的 API key。留空表示匿名访问（仅限内网/开发）。
    #: 生产环境应通过 Secret 注入，绝不写进镜像或代码仓库。
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
    #: 模型服务的 Bearer token（如 OpenAI / 托管 vLLM）。本地 LM Studio 留空即可。
    model_api_key: str = ""

    # ------------------------------------------------------------------
    # 出站 HTTP 超时（每类调用单独可调）
    # ------------------------------------------------------------------
    # 生产原则：任何出站调用都必须有显式超时，否则一个卡死的下游会耗尽
    # 本服务的工作线程。数值按“依赖的 P99 延迟 + 余量”设定，而不是拍脑袋。
    http_timeout_seconds: float = 10.0       #: 默认出站超时（Qdrant 管理操作等）
    probe_timeout_seconds: float = 2.0       #: 健康探测超时——必须短，避免拖垮健康检查
    embedding_timeout_seconds: float = 60.0  #: 批量 Embedding 可能较慢
    chat_timeout_seconds: float = 90.0       #: LLM 生成是最慢的一环
    index_timeout_seconds: float = 120.0     #: 批量写入 Qdrant

    # ------------------------------------------------------------------
    # 安全
    # ------------------------------------------------------------------
    #: 允许的 CORS 来源，逗号分隔（如 "https://kb.example.com,https://admin.example.com"）。
    #: 开发默认 "*" 方便前端调试；production 环境保持 "*" 会触发启动告警。
    cors_allow_origins: str = "*"

    #: API 访问密钥。设置后所有 /api/ 路径要求 X-API-Key 或 Authorization: Bearer。
    #: /livez /readyz /metrics 始终豁免，供编排器与 Prometheus 使用。
    #: 生产环境通过 Secret 注入；本框架的密钥比较使用常数时间算法防时序攻击。
    api_key: str = ""

    #: 每个客户端 IP 每分钟允许的 /api/ 请求数；0 表示关闭。
    #: 内置限流器是单进程内存实现，适合单副本；多副本部署请在网关层
    #: （Nginx/Envoy/云 WAF）或用 Redis 限流，详见 docs/production-checklist.md。
    rate_limit_per_minute: int = 0

    #: 单文件上传上限（MB）。同时在反向代理层设置 client_max_body_size 双重保护。
    max_upload_mb: int = 64

    # ------------------------------------------------------------------
    # 可观测性（详见 docs/observability.md）
    # ------------------------------------------------------------------
    #: 日志级别：debug | info | warning | error
    log_level: str = "info"
    #: 日志格式：text（开发友好）| json（生产必选——可被 Loki/ELK 直接解析，
    #: 且每条日志自动携带 request_id 与 OTel trace_id，实现日志↔链路互跳）。
    log_format: str = "text"

    #: 是否启用 OpenTelemetry 分布式追踪。默认关闭（显式开启原则），
    #: 开启后需要 `pip install ".[observability]"` 并部署 OTLP 接收端
    #: （docker compose --profile observability up 即可获得 Collector + Jaeger）。
    otel_enabled: bool = False
    #: OTLP HTTP 端点（4318 端口）。Compose 内为 http://otel-collector:4318。
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    #: 上报到追踪后端的 service.name，多服务共用一个 Jaeger 时用于区分。
    otel_service_name: str = "openrag-forge"
    #: 采样率 0.0~1.0。开发环境 1.0 全采样；高流量生产建议 0.05~0.2，
    #: 并依赖 ParentBased 采样器保证同一条链路的采样决策一致。
    otel_sample_ratio: float = 1.0

    #: 是否暴露 Prometheus /metrics 端点（需要 observability extra）。
    metrics_enabled: bool = True

    # ------------------------------------------------------------------
    # 检索与业务 Trace
    # ------------------------------------------------------------------
    retrieval_score_threshold: float = 0.5
    trace_persistence: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_prefix="OPENRAG_", extra="ignore")

    # ------------------------------------------------------------------
    # 派生路径
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 生产就绪校验
    # ------------------------------------------------------------------
    def production_warnings(self) -> list[str]:
        """返回当前配置在生产环境下的风险清单。

        设计取舍：返回告警而不是直接抛异常退出，是因为本框架支持"降级可用"
        （模型服务未就绪时上传/解析仍可工作）。但每一条告警都会打进启动日志、
        暴露在 /api/v1/health 的 production_readiness 字段里，让风险可见、可审计。
        """
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
        return warnings


settings = Settings()
