from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── 代码常量（不入 .env，全仓单一事实源，见 docs/DESIGN.md §1.1） ──
VISION_TIMEOUT_SECONDS: int = 60
MAX_IMAGE_PIXELS: int = 40_000_000
MAX_FILES_PER_REQUEST: int = 10
SLA_SCAN_INTERVAL_SECONDS: int = 60


class Settings(BaseSettings):
    """应用运行时配置（从 .env 与环境变量加载）。

    注意：jwt_secret_key 无默认值，缺失时实例化即抛 pydantic.ValidationError。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Runtime ──
    app_env: str = "development"
    tz: str = "Asia/Shanghai"

    # ── PostgreSQL & psycopg v3 ──
    tomori_pg_host: str = "127.0.0.1"
    tomori_pg_port: int = 25432
    tomori_pg_user: str = "tomori"
    tomori_pg_password: str = "tomori_dev_password"
    tomori_pg_db: str = "tomori_dev"
    tomori_pg_test_db: str = "tomori_test"

    database_url: str = "postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_dev"
    test_database_url: str = (
        "postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_test"
    )
    e2e_database_url: str = (
        "postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_test"
    )

    # ── Local Service Ports & CORS ──
    backend_host: str = "127.0.0.1"
    backend_port: int = 18141
    frontend_host: str = "127.0.0.1"
    frontend_port: int = 15173
    cors_origins: str = "http://127.0.0.1:15173,http://localhost:15173"

    # ── Auth / Security (jwt_secret_key 禁止设默认值) ──
    jwt_secret_key: str = Field(min_length=1)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=60, ge=1)

    # ── Static Inspection & Repair Photo Storage ──
    upload_dir: Path = Path("./uploads")
    max_upload_size_mb: int = Field(default=15, ge=1)
    allowed_image_extensions: str = "jpg,jpeg,png,webp"

    # ── Ultralytics YOLO26 Vision Inference ──
    yolo_weights_path: str = "./vision/weights/best.pt"
    yolo_fallback_model: str = "yolo26s.pt"
    yolo_device: str = "cpu"
    yolo_conf_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    yolo_iou_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    yolo_img_size: int = Field(default=1280, ge=64)
    yolo_inference_enabled: bool = True
    yolo_inference_mode: str = "auto"
    yolo_autoinstall: bool = False

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def access_token_expire_minutes(self) -> int:
        return self.jwt_access_token_expire_minutes

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def allowed_image_extension_set(self) -> set[str]:
        return {
            ext.strip().lower() for ext in self.allowed_image_extensions.split(",") if ext.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """惰性加载并缓存配置；缺失 JWT_SECRET_KEY 时触发 ValidationError。"""
    return Settings()
