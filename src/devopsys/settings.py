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

    out_dir: str = Field(default="out")

settings = Settings()
