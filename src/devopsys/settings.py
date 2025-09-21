from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEVOPSYS_", env_file=".env", extra="ignore")

    backend: str = Field(default="dummy")
    model: str = Field(default="codellama:7b-instruct")
    ollama_host: str = Field(default="http://127.0.0.1:11434")

    temperature: float = 0.2
    max_tokens: int = 1024
    ollama_timeout: float = 300.0

    openai_api_key: str = Field(default="")
    openai_base_url: str = Field(default="https://api.openai.com/v1")
    openai_model: str = Field(default="gpt-4o-mini")
    openai_timeout: float = 120.0
    openai_system_prompt: str | None = Field(default=None)

    deepseek_api_key: str = Field(default="")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1")
    deepseek_model: str = Field(default="deepseek-coder")
    deepseek_timeout: float = 120.0
    deepseek_system_prompt: str | None = Field(default=None)

    out_dir: str = Field(default="out")

settings = Settings()
