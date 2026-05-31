from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    OLLAMA_URL: str = "http://127.0.0.1:11434/api/generate"
    OLLAMA_MODEL: str = "qwen3.5:9b"
    ROUTING_MODEL: str = "qwen3.5:9b"
    OLLAMA_THINK: bool = False  # Qwen3.5 thinking mode hides tokens in `thinking` field
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_REPLY_TIMEOUT_SEC: float = 120.0
    LOG_LEVEL: str = "INFO"
    LOG_FILE_PATH: str = ""  # default: <project>/logs/backend.log
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    DATABASE_PATH: str = ""  # default: backend/platform.db (set in database.py)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def api_base_url(self) -> str:
        return f"http://{self.API_HOST}:{self.API_PORT}"


settings = Settings()
