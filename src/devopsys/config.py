
from __future__ import annotations
from pydantic import BaseModel
from pathlib import Path
import os

ENV_FILE = Path(".env")

class Config(BaseModel):
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    safe_mode: bool = True
    monitor_sources: list[str] = ["top","df","vmstat"]
    monitor_interval_sec: int = 30
    # exec streaming
    exec_stream: bool = True
    exec_chunk_lines: int = 10
    exec_edit_ms: int = 1200
    sudo_noninteractive: bool = True
    # logging
    log_level: str = "INFO"            # DEBUG|INFO|WARNING|ERROR
    log_file: str | None = None        # e.g., devopsys.log
    log_console: bool = True
    log_to_telegram: bool = False      # duplicate key events to Telegram

    @classmethod
    def from_env(cls) -> "Config":
        data: dict = {}
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        data["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or None
        data["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID") or None
        data["safe_mode"] = (os.environ.get("DEVOPSYS_SAFE","true").lower() == "true")
        srcs = os.environ.get("DEVOPSYS_MONITOR_SOURCES","top,df,vmstat").split(",")
        data["monitor_sources"] = [s.strip() for s in srcs if s.strip()]
        data["monitor_interval_sec"] = int(os.environ.get("DEVOPSYS_MONITOR_INTERVAL","30"))
        # exec streaming
        data["exec_stream"] = (os.environ.get("DEVOPSYS_EXEC_STREAM","true").lower() == "true")
        data["exec_chunk_lines"] = int(os.environ.get("DEVOPSYS_EXEC_CHUNK_LINES","10"))
        data["exec_edit_ms"] = int(os.environ.get("DEVOPSYS_EXEC_EDIT_MS","1200"))
        data["sudo_noninteractive"] = (os.environ.get("DEVOPSYS_SUDO_NONINTERACTIVE","true").lower() == "true")
        # logging
        data["log_level"] = os.environ.get("DEVOPSYS_LOG_LEVEL","INFO").upper()
        data["log_file"] = os.environ.get("DEVOPSYS_LOG_FILE") or None
        data["log_console"] = (os.environ.get("DEVOPSYS_LOG_CONSOLE","true").lower() == "true")
        data["log_to_telegram"] = (os.environ.get("DEVOPSYS_LOG_TO_TELEGRAM","false").lower() == "true")
        return cls(**data)

def save_to_env(cfg: Config, path: str | None = None) -> None:
    p = Path(path) if path else ENV_FILE
    lines: list[str] = []
    lines.append(f"TELEGRAM_BOT_TOKEN={cfg.telegram_bot_token or ''}")
    lines.append(f"TELEGRAM_CHAT_ID={cfg.telegram_chat_id or ''}")
    lines.append(f"DEVOPSYS_SAFE={'true' if cfg.safe_mode else 'false'}")
    lines.append("DEVOPSYS_MONITOR_SOURCES=" + ",".join(cfg.monitor_sources))
    lines.append(f"DEVOPSYS_MONITOR_INTERVAL={cfg.monitor_interval_sec}")
    # exec streaming
    lines.append(f"DEVOPSYS_EXEC_STREAM={'true' if cfg.exec_stream else 'false'}")
    lines.append(f"DEVOPSYS_EXEC_CHUNK_LINES={cfg.exec_chunk_lines}")
    lines.append(f"DEVOPSYS_EXEC_EDIT_MS={cfg.exec_edit_ms}")
    lines.append(f"DEVOPSYS_SUDO_NONINTERACTIVE={'true' if cfg.sudo_noninteractive else 'false'}")
    # logging
    lines.append(f"DEVOPSYS_LOG_LEVEL={cfg.log_level}")
    lines.append(f"DEVOPSYS_LOG_FILE={cfg.log_file or ''}")
    lines.append(f"DEVOPSYS_LOG_CONSOLE={'true' if cfg.log_console else 'false'}")
    lines.append(f"DEVOPSYS_LOG_TO_TELEGRAM={'true' if cfg.log_to_telegram else 'false'}")
    if (k:=os.environ.get('OPENAI_API_KEY')):
        lines.append(f"OPENAI_API_KEY={k}")
    p.write_text("\n".join(lines) + "\n")
