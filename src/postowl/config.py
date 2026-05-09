from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_DIR = Path.home() / ".postowl"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"
DEFAULT_DB_PATH = DEFAULT_CONFIG_DIR / "postowl.db"
DEFAULT_CHROMA_PATH = DEFAULT_CONFIG_DIR / "chroma"


class LLMConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    chat_model: str = "gpt-4o-mini"
    temperature: float = 0.3
    max_tokens: int = 2048


class EmbeddingConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "text-embedding-3-small"


class TelegramConfig(BaseModel):
    bot_token: str = ""
    allowed_user_ids: list[int] = Field(default_factory=list)


class SchedulerConfig(BaseModel):
    fetch_interval_minutes: int = 10
    reminder_check_interval_seconds: int = 60
    max_workers: int = 4
    use_idle: bool = True
    idle_reconnect_interval_seconds: int = 300


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="POSTOWL_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    config_dir: Path = DEFAULT_CONFIG_DIR
    db_path: Path = DEFAULT_DB_PATH
    chroma_path: Path = DEFAULT_CHROMA_PATH
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)


def load_config(config_path: Path | None = None) -> Settings:
    path = config_path or DEFAULT_CONFIG_FILE
    settings = Settings()
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        if "llm" in data:
            settings.llm = LLMConfig(**data["llm"])
        if "embedding" in data:
            settings.embedding = EmbeddingConfig(**data["embedding"])
        if "telegram" in data:
            settings.telegram = TelegramConfig(**data["telegram"])
        if "scheduler" in data:
            settings.scheduler = SchedulerConfig(**data["scheduler"])
        if "db_path" in data:
            settings.db_path = Path(data["db_path"])
        if "chroma_path" in data:
            settings.chroma_path = Path(data["chroma_path"])
    return settings


def save_config(settings: Settings, config_path: Path | None = None) -> None:
    path = config_path or DEFAULT_CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "llm": settings.llm.model_dump(),
        "embedding": settings.embedding.model_dump(),
        "telegram": settings.telegram.model_dump(),
        "scheduler": settings.scheduler.model_dump(),
    }
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
