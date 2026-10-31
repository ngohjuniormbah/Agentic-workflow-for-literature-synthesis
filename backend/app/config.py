from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Synthesis API"
    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///./synthesis.db"
    cors_origins: str = "http://localhost:3000"
    openalex_email: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_http_referer: str = ""
    openrouter_app_name: str = "Synthesis Literature Review"
    openrouter_qwen_model_id: str = ""
    openrouter_kimi_model_id: str = ""
    openrouter_deepseek_model_id: str = ""
    openai_api_key: str = ""
    openai_model_id: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 90.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
